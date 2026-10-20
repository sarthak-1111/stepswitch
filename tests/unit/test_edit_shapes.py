"""Edit format version 2 (ADR 0038): the edit tool takes the shapes models send.

In the Phase 11 router rerun about half of all edit calls were refused for their shape, not
their content (ADR 0037). Each shape below was sent by a model in that run (the arguments are
copied with this test's file in place of the task's). Version 2 accepts them where the change
they ask for is not in doubt, and refuses with a precise message where it is:

- excerpt edits that name lines by their old text (`old`/`new`, `search`/`replace`,
  `delete`/`insert` holding text), found as whole lines, then whatever their indentation, then
  as text inside lines that occurs once;
- partial anchors: a bare hash names the one line that has it; a bare line number is taken
  only when read showed that line, as it is now;
- text patches in the `*** Begin Patch` format and unified diffs, checked whole before any file
  is written;
- empty defaults beside the real argument, `hash` given apart from `line`, a stray `create`,
  one edit given at the top level, and no `path` when the anchors fit one file read so far.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from stepswitch.context import edits as E
from stepswitch.context import patch as P
from stepswitch.workers import tools as T

PAGER = '''"""Pages of search results."""

from collections.abc import Sequence


def get_page(results: Sequence[str], number: int, per_page: int) -> list[str]:
    """Return the results shown on page `number`."""
    if number < 1:
        raise ValueError("page numbers start at 1")
    if per_page < 1:
        raise ValueError("per_page must be at least 1")
    start = number * per_page
    return list(results[start : start + per_page])
'''
FIXED_LINE = "    start = (number - 1) * per_page"
TWINS = "def first():\n    return 1\n\n\ndef second():\n    return 1\n"
ROW = re.compile(r"^(\d+):([a-z]{2})\|(.*)$")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / "pkg").mkdir(parents=True)
    (project / "pager.py").write_text(PAGER, encoding="utf-8")
    (project / "pkg" / "twins.py").write_text(TWINS, encoding="utf-8")
    (project / "secrets").mkdir()
    (project / "secrets" / "key.txt").write_text("KEY=1\n", encoding="utf-8")
    return project


@pytest.fixture
def box(root: Path) -> T.Toolbox:
    config = SimpleNamespace(privacy=SimpleNamespace(deny_paths=["secrets/**"]))
    return T.Toolbox(root, config=config, max_output_chars=6000)


def read(box: T.Toolbox, path: str) -> dict[str, tuple[int, str]]:
    """{line text: (line, hash)} as read shows them (the first row of each text)."""
    shown = box.execute("read", {"path": path})
    assert not shown.is_error, shown.text
    rows: dict[str, tuple[int, str]] = {}
    for row in shown.text.split("\n")[1:]:
        match = ROW.match(row)
        if match:
            rows.setdefault(match.group(3), (int(match.group(1)), match.group(2)))
    return rows


def edit(box: T.Toolbox, arguments: Any, *, name: str = "edit") -> T.ToolOutput:
    return box.execute(name, arguments)


def text(root: Path, path: str = "pager.py") -> str:
    return (root / path).read_text(encoding="utf-8")


FIXED = PAGER.replace("    start = number * per_page", FIXED_LINE)


# -- shapes the model sent --------------------------------------------------------------------


def test_delete_and_insert_at_a_line_replace_it(box: T.Toolbox, root: Path) -> None:
    rows = read(box, "pager.py")
    line, digest = rows["    start = number * per_page"]
    # Sent with an empty `text` beside the edits, which version 1 refused as "not both".
    done = edit(
        box,
        {
            "path": "pager.py",
            "create": False,
            "edits": [{"delete": 1, "line": line}, {"insert": FIXED_LINE, "line": line}],
            "text": "",
        },
    )
    assert not done.is_error, done.text
    assert text(root) == FIXED
    # The same with the bare hash for the line.
    (root / "pager.py").write_text(PAGER, encoding="utf-8")
    read(box, "pager.py")
    done = edit(
        box,
        {
            "edits": [{"delete": 1, "line": digest}, {"insert": FIXED_LINE, "line": digest}],
            "path": "pager.py",
            "text": "",
        },
    )
    assert not done.is_error, done.text
    assert text(root) == FIXED


@pytest.mark.parametrize(
    "change",
    [
        # `delete` and `insert` holding the old and the new line, with a line hint.
        {"delete": "    start = number * per_page", "insert": FIXED_LINE, "line": 12},
        # The same with an anchor as the hint.
        {"anchor": "12:zz", "delete": "    start = number * per_page", "insert": FIXED_LINE},
        # search/replace, without the indentation.
        {"replace": FIXED_LINE.strip(), "search": "start = number * per_page", "type": "replace"},
        # old/new.
        {"old": "    start = number * per_page", "new": FIXED_LINE},
        {"old_string": "    start = number * per_page\n", "new_string": FIXED_LINE + "\n"},
    ],
)
def test_old_text_names_the_lines(box: T.Toolbox, root: Path, change: dict[str, Any]) -> None:
    done = edit(box, {"path": "pager.py", "create": False, "edits": [change], "text": ""})
    assert not done.is_error, done.text
    assert text(root) == FIXED


@pytest.mark.parametrize(
    "shape",
    [
        lambda n, h: {"hash": h, "text": FIXED_LINE},
        lambda n, h: {"hash": h, "line": n, "text": FIXED_LINE},
        lambda n, h: {"end": h, "op": "replace", "start": h, "text": FIXED_LINE},
        lambda n, h: {"anchor": h, "content": FIXED_LINE},
        lambda n, h: {"content": FIXED_LINE, "line": n, "type": "replace"},
        lambda n, h: {"delete": h, "line": n, "text": FIXED_LINE},
        lambda n, h: {"anchor": f"{h}|    start = number * per_page", "content": FIXED_LINE},
        lambda n, h: {"anchor": h, "delete": 1, "insert": FIXED_LINE},
        lambda n, h: {"content": FIXED_LINE, "line_end": n, "line_start": n},
        lambda n, h: [
            {"anchor": h, "delete": True, "line": n},
            {"anchor": h, "line": n, "text": FIXED_LINE},
        ],
    ],
)
def test_partial_anchors_name_lines_read_showed(box: T.Toolbox, root: Path, shape: Any) -> None:
    line, digest = read(box, "pager.py")["    start = number * per_page"]
    edits = shape(line, digest)
    edits = edits if isinstance(edits, list) else [edits]
    done = edit(box, {"path": "pager.py", "create": False, "edits": edits})
    assert not done.is_error, done.text
    assert text(root) == FIXED


def test_lines_deleted_one_by_one_and_text_put_before_them(box: T.Toolbox, root: Path) -> None:
    rows = read(box, "pager.py")
    first, _ = rows["    if number < 1:"]
    edits: list[dict[str, Any]] = [{"delete": True, "line": n} for n in range(first, first + 4)]
    edits.append({"line": first, "op": "insert", "text": "    check(number, per_page)"})
    done = edit(box, {"path": "pager.py", "edits": edits})
    assert not done.is_error, done.text
    assert "    check(number, per_page)\n    start = number * per_page\n" in text(root)
    assert "raise ValueError" not in text(root)
    # An insert with no anchor goes where the deletes removed one block of lines, and only there.
    (root / "pager.py").write_text(PAGER, encoding="utf-8")
    read(box, "pager.py")
    edits[-1] = {"insert": "    check(number, per_page)"}
    done = edit(box, {"path": "pager.py", "edits": edits})
    assert not done.is_error, done.text
    assert "    check(number, per_page)\n    start = number * per_page\n" in text(root)
    (root / "pager.py").write_text(PAGER, encoding="utf-8")
    read(box, "pager.py")
    for scattered in ([edits[-1]], [edits[0], edits[2], edits[-1]]):
        refused = edit(box, {"path": "pager.py", "edits": scattered})
        assert refused.is_error and "insert needs `after` or `before`" in refused.text
    assert text(root) == PAGER


def test_inserts_at_a_line_or_after_a_bare_hash(box: T.Toolbox, root: Path) -> None:
    rows = read(box, "pager.py")
    line, _ = rows["    start = number * per_page"]
    _, after = rows['    """Return the results shown on page `number`."""']
    done = edit(
        box,
        {
            "path": "pager.py",
            "edits": [
                {"line": line, "op": "insert", "text": "    # pages count from 1"},
                {"anchor": after, "insert": "    results = list(results)", "position": "after"},
            ],
        },
    )
    assert not done.is_error, done.text
    lines = text(root).splitlines()
    assert lines[6:8] == [
        '    """Return the results shown on page `number`."""',
        "    results = list(results)",
    ]
    assert lines[12:14] == ["    # pages count from 1", "    start = number * per_page"]


def test_one_edit_at_the_top_level_and_no_path(box: T.Toolbox, root: Path) -> None:
    line, digest = read(box, "pager.py")["    start = number * per_page"]
    read(box, "pkg/twins.py")
    anchor = f"{line}:{digest}"
    # No path: the anchor was shown for pager.py only, so the edit goes there, and says so.
    done = edit(box, {"op": "replace", "start": anchor, "end": anchor, "text": FIXED_LINE})
    assert not done.is_error, done.text
    assert done.text.startswith("[no `path` was given: the edit went to pager.py")
    assert done.data["inferred_path"] is True and text(root) == FIXED
    # Old text alone, at the top level, also finds its file.
    done = edit(box, {"old": "        return 1", "new": "        return 2"})
    assert done.is_error and "missing `path`" in done.text  # twins.py has it twice: no guess
    done = edit(
        box, {"old_str": FIXED_LINE, "new_str": "    start = (number - 1) * per_page  # 1-based"}
    )
    assert not done.is_error, done.text
    assert "# 1-based" in text(root)


def test_create_rewrites_a_file_the_model_has_read(box: T.Toolbox, root: Path) -> None:
    refused = edit(box, {"path": "pager.py", "create": True, "text": FIXED})
    assert refused.is_error and "already exists" in refused.text  # never read: no rewrite
    assert text(root) == PAGER
    read(box, "pager.py")
    done = edit(box, {"path": "pager.py", "create": True, "text": FIXED})
    assert not done.is_error, done.text
    assert text(root) == FIXED
    # A stray `create` beside edits to an existing file is ignored.
    line, digest = read(box, "pager.py")[FIXED_LINE]
    edits = [{"op": "replace", "start": f"{line}:{digest}", "text": "    start = 0"}]
    done = edit(box, {"path": "pager.py", "create": True, "edits": edits})
    assert not done.is_error, done.text


# -- what stays refused -------------------------------------------------------------------------


def test_a_bare_line_number_needs_a_read_of_that_line_as_it_is(box: T.Toolbox, root: Path) -> None:
    refused = edit(box, {"path": "pager.py", "edits": [{"line": 12, "text": FIXED_LINE}]})
    assert refused.is_error and "has no hash" in refused.text and "12:" in refused.text
    read(box, "pager.py")
    (root / "pager.py").write_text("# moved\n" + PAGER, encoding="utf-8")  # changed since
    refused = edit(box, {"path": "pager.py", "edits": [{"line": 12, "text": FIXED_LINE}]})
    assert refused.is_error and "was not shown to you as it is now" in refused.text
    assert text(root) == "# moved\n" + PAGER


def test_a_bare_hash_must_name_one_line(box: T.Toolbox, root: Path) -> None:
    doc = E.TextDoc.decode(PAGER.encode())
    hashes = E.line_hashes(doc.lines)
    absent = next(
        a + b
        for a in "abcdefghijklmnopqrstuvwxyz"
        for b in "abcdefghijklmnopqrstuvwxyz"
        if a + b not in hashes
    )
    refused = edit(box, {"path": "pager.py", "edits": [{"start": absent, "text": "x"}]})
    assert refused.is_error and "matches no line" in refused.text
    with pytest.raises(E.EditError, match="matches several lines"):
        E.resolve_anchor(
            E.TextDoc.decode(b"a\nb\n"), ["xx", "xx"], E.Anchor(0, "xx"), None, "start"
        )


def test_old_text_that_fits_several_places_needs_a_hint(box: T.Toolbox, root: Path) -> None:
    change = {"old": "    return 1", "new": "    return 2"}
    refused = edit(box, {"path": "pkg/twins.py", "edits": [change]})
    assert refused.is_error and "matches 2 places" in refused.text
    assert text(root, "pkg/twins.py") == TWINS
    done = edit(box, {"path": "pkg/twins.py", "edits": [{**change, "line": 6}]})
    assert not done.is_error, done.text
    assert text(root, "pkg/twins.py") == TWINS.replace(
        "def second():\n    return 1", "def second():\n    return 2"
    )
    missing = edit(box, {"path": "pkg/twins.py", "edits": [{"old": "    return 3", "new": "x"}]})
    assert missing.is_error and "not in the file" in missing.text


def test_old_text_without_indentation_gets_the_files(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text("class A:\n    def f(self):\n        return 1\n", encoding="utf-8")
    E.edit_file(
        path,
        edits=[
            {"old": "def f(self):\n    return 1", "new": "def f(self):\n    x = 2\n    return x"}
        ],
    )
    assert (
        path.read_text(encoding="utf-8")
        == "class A:\n    def f(self):\n        x = 2\n        return x\n"
    )
    # Indentation that differs by more than one amount is not guessed.
    path.write_text("if a:\n    b = 1\n        c = 2\n", encoding="utf-8")
    with pytest.raises(E.EditError, match="not by one consistent amount"):
        E.edit_file(path, edits=[{"old": "if a:\nb = 1\nc = 2", "new": "x"}])


def test_old_text_inside_a_line_is_replaced_as_a_string_replace_would(tmp_path: Path) -> None:
    path = tmp_path / "c.py"
    path.write_text(
        "def centroid(x, intensities):\n    weights = intensities**2\n    return weights\n",
        encoding="utf-8",
    )
    E.edit_file(path, edits=[{"search": "intensities**2", "replace": "intensities"}])
    assert "    weights = intensities\n" in path.read_text(encoding="utf-8")
    # Not inside a word: `x = 1` is not in `max = 1`.
    path.write_text("max = 1\n", encoding="utf-8")
    with pytest.raises(E.EditError, match="not in the file"):
        E.edit_file(path, edits=[{"old": "x = 1", "new": "x = 2"}])
    # Twice is ambiguous; a line hint chooses.
    path.write_text("a = f(1)\nb = f(1)\n", encoding="utf-8")
    with pytest.raises(E.EditError, match="occurs 2 times"):
        E.edit_file(path, edits=[{"old": "f(1)", "new": "f(2)"}])
    E.edit_file(path, edits=[{"old": "f(1)", "new": "f(2)", "line": 2}])
    assert path.read_text(encoding="utf-8") == "a = f(1)\nb = f(2)\n"


def test_new_without_old_never_replaces_a_whole_file(box: T.Toolbox, root: Path) -> None:
    refused = edit(box, {"path": "pager.py", "new": FIXED_LINE})
    assert refused.is_error and "`new` needs `old`" in refused.text
    assert text(root) == PAGER
    made = edit(box, {"path": "pkg/fresh.py", "new": "x = 1\n"})
    assert not made.is_error and text(root, "pkg/fresh.py") == "x = 1\n"


def test_empty_defaults_beside_the_real_argument(box: T.Toolbox, root: Path) -> None:
    read(box, "pkg/twins.py")
    done = edit(box, {"path": "pkg/twins.py", "edits": [], "text": "x = 1\n"})
    assert not done.is_error, done.text
    assert text(root, "pkg/twins.py") == "x = 1\n"


# -- patches -------------------------------------------------------------------------------


UPDATE = """*** Begin Patch
*** Update File: pager.py
@@ def get_page(results: Sequence[str], number: int, per_page: int) -> list[str]:
     if per_page < 1:
         raise ValueError("per_page must be at least 1")
-    start = number * per_page
+    start = (number - 1) * per_page
     return list(results[start : start + per_page])
*** Add File: pkg/notes.md
+# Notes
+
+Pages count from 1.
*** End Patch
"""


def test_a_begin_patch_updates_and_adds_files(box: T.Toolbox, root: Path) -> None:
    done = edit(box, {"patch": UPDATE})  # no path: the patch names its files
    assert not done.is_error, done.text
    assert text(root) == FIXED
    assert text(root, "pkg/notes.md") == "# Notes\n\nPages count from 1.\n"
    assert done.changed_files == ["pager.py", "pkg/notes.md"]
    assert (
        done.data["patch"] is True and "+    start = (number - 1) * per_page" in done.data["diff"]
    )
    assert "updated lines with fresh anchors" in done.text


def test_a_patch_reports_what_it_changed_in_the_shape_of_an_edit(
    box: T.Toolbox, root: Path
) -> None:
    both = edit(box, {"patch": UPDATE}).data
    assert both["changed"] is True and "path" not in both
    assert [(f["path"], f["changed"], f["created"]) for f in both["files"]] == [
        ("pager.py", True, False),
        ("pkg/notes.md", True, True),
    ]
    one = UPDATE.split("*** Add File")[0].replace("(number - 1)", "(number - 2)")
    one = one.replace("-    start = number * per_page", "-    start = (number - 1) * per_page")
    data = edit(box, {"patch": one + "*** End Patch\n"}).data
    # the agent reads a one-file patch like a one-file edit (path, changed, added, removed)
    assert data["path"] == "pager.py" and data["changed"] is True
    assert data["added"] == 1 and data["removed"] == 1


@pytest.mark.parametrize(
    "wrap",
    [
        lambda body: body,
        lambda body: f"\n\n{body}\n\n",
        lambda body: f"apply_patch <<'EOF'\n{body}EOF\n",
        lambda body: body.replace("*** End Patch\n", ""),
        lambda body: body.replace("*** Begin Patch\n", ""),
        lambda body: body.replace("\n", "\r\n"),
    ],
)
def test_patches_sent_as_the_whole_argument(box: T.Toolbox, root: Path, wrap: Any) -> None:
    done = edit(box, wrap(UPDATE), name="apply_patch")
    assert not done.is_error, done.text
    assert text(root) == FIXED and (root / "pkg" / "notes.md").exists()


def begin(*rows: str) -> str:
    """A patch in the `*** Begin Patch` format."""
    return "\n".join(["*** Begin Patch", *rows, "*** End Patch"])


SECOND_RETURNS_2 = TWINS.replace("def second():\n    return 1", "def second():\n    return 2")


def test_a_refused_patch_changes_nothing(box: T.Toolbox, root: Path) -> None:
    broken = UPDATE.replace(
        "*** Add File: pkg/notes.md",
        "*** Update File: pkg/twins.py\n@@\n-    return 7\n+    return 8\n"
        "*** Add File: pkg/notes.md",
    )
    refused = edit(box, {"patch": broken})
    assert refused.is_error and "pkg/twins.py hunk 1" in refused.text
    assert "Nothing was changed" in refused.text
    assert text(root) == PAGER and not (root / "pkg" / "notes.md").exists()
    for patch in (
        begin("*** Delete File: pager.py"),
        begin("*** Update File: pager.py", "*** Move to: other.py", "@@", "-x", "+y"),
        begin("*** Add File: pager.py", "+x"),
        begin("*** Update File: ../outside.py", "@@", "-x", "+y"),
        begin("*** Add File: secrets/new.txt", "+x"),
    ):
        assert edit(box, {"patch": patch}).is_error, patch
    assert text(root) == PAGER
    assert sorted(p.name for p in (root / "secrets").iterdir()) == ["key.txt"]


def test_hunks_are_found_forward_and_an_at_line_tells_twins_apart(
    box: T.Toolbox, root: Path
) -> None:
    ambiguous = begin("*** Update File: pkg/twins.py", "@@", "-    return 1", "+    return 2")
    refused = edit(box, {"patch": ambiguous})
    assert refused.is_error and "match 2 places" in refused.text
    second = ambiguous.replace("@@\n", "@@ def second():\n")
    assert not edit(box, {"patch": second}).is_error
    assert text(root, "pkg/twins.py") == SECOND_RETURNS_2
    # Two hunks: the second is searched after the first.
    both = begin(
        "*** Update File: pkg/twins.py",
        "@@",
        " def first():",
        "-    return 1",
        "+    return 0",
        "@@",
        "-    return 2",
        "+    return 3",
    )
    assert not edit(box, {"patch": both}).is_error
    expected = "def first():\n    return 0\n\n\ndef second():\n    return 3\n"
    assert text(root, "pkg/twins.py") == expected


def test_added_lines_alone_go_after_the_at_line_or_at_the_end(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text("class A:\n    x = 1\n", encoding="utf-8")

    def apply(body: str) -> None:
        changes = P.parse(body)
        P.write(P.plan(changes, lambda raw, existing: (tmp_path / raw, raw)), lambda rel: None)

    apply(begin("*** Update File: m.py", "@@ class A:", "+    y = 0"))
    assert path.read_text(encoding="utf-8") == "class A:\n    y = 0\n    x = 1\n"
    apply(begin("*** Update File: m.py", "@@", "+z = 2"))
    assert path.read_text(encoding="utf-8").endswith("    x = 1\nz = 2\n")
    (tmp_path / "empty.py").write_text("", encoding="utf-8")
    apply(begin("*** Update File: empty.py", "@@", "+a = 1", "@@", "+b = 2"))
    assert (tmp_path / "empty.py").read_text(encoding="utf-8") == "a = 1\nb = 2\n"
    # `*** End of File` prefers the match that ends the file.
    path.write_text("x = 1\nx = 1\n", encoding="utf-8")
    apply(begin("*** Update File: m.py", "@@", "-x = 1", "+x = 9", "*** End of File"))
    assert path.read_text(encoding="utf-8") == "x = 1\nx = 9\n"
    # A file named twice is changed by its second section as the first left it.
    apply(
        begin(
            *("*** Update File: m.py", "@@", "-x = 9", "+x = 8"),
            *("*** Update File: m.py", "@@", "-x = 8", "+x = 7"),
        )
    )
    assert path.read_text(encoding="utf-8") == "x = 1\nx = 7\n"


def test_unified_diffs(box: T.Toolbox, root: Path) -> None:
    diff = """--- a/pager.py
+++ b/pager.py
@@ -10,4 +10,4 @@ def get_page(results: Sequence[str], number: int, per_page: int) -> list[str]:
     if per_page < 1:
         raise ValueError("per_page must be at least 1")
-    start = number * per_page
+    start = (number - 1) * per_page
     return list(results[start : start + per_page])
"""
    done = edit(box, {"diff": diff})
    assert not done.is_error, done.text
    assert text(root) == FIXED
    # The old start line is a hint between twins.
    twins = (
        "--- a/pkg/twins.py\n+++ b/pkg/twins.py\n@@ -6,1 +6,1 @@\n-    return 1\n+    return 2\n"
    )
    assert not edit(box, twins, name="apply_patch").is_error
    assert text(root, "pkg/twins.py") == SECOND_RETURNS_2
    # A removed line that reads `-- ...` is not a file header.
    (root / "q.sql").write_text("-- note\nselect 1;\n", encoding="utf-8")
    removed = "--- a/q.sql\n+++ b/q.sql\n@@ -1,2 +1,1 @@\n--- note\n select 1;\n"
    assert not edit(box, {"patch": removed}).is_error
    assert text(root, "q.sql") == "select 1;\n"
    created = "--- /dev/null\n+++ b/pkg/made.py\n@@ -0,0 +1,2 @@\n+a = 1\n+b = 2\n"
    assert not edit(box, {"patch": created}).is_error
    assert text(root, "pkg/made.py") == "a = 1\nb = 2\n"
    # In the begin-patch format, a unified header is a line hint too.
    (root / "pkg" / "twins.py").write_text(TWINS, encoding="utf-8")
    mixed = begin(
        "*** Update File: pkg/twins.py", "@@ -2,1 +2,1 @@", "-    return 1", "+    return 5"
    )
    assert not edit(box, {"patch": mixed}).is_error
    assert text(root, "pkg/twins.py") == TWINS.replace("    return 1\n", "    return 5\n", 1)


def test_a_patch_to_a_misremembered_directory_finds_the_only_file(
    box: T.Toolbox, root: Path
) -> None:
    patch = begin(
        "*** Update File: src/twins.py", "@@ def second():", "-    return 1", "+    return 4"
    )
    done = edit(box, {"patch": patch})
    assert not done.is_error, done.text
    assert done.text.startswith("[src/twins.py does not exist: the patch went to pkg/twins.py")
    assert text(root, "pkg/twins.py").endswith("    return 4\n")


def test_typographic_punctuation_and_the_files_own_context_lines(tmp_path: Path) -> None:
    path = tmp_path / "m.py"
    path.write_text('msg = "UTF-8 only" \nflag = 1\n', encoding="utf-8")  # trailing space

    def apply(body: str) -> None:
        P.write(P.plan(P.parse(body), lambda raw, existing: (tmp_path / raw, raw)), lambda r: None)

    # The model wrote a non-breaking hyphen and curly quotes; the file has ASCII.
    apply(begin("*** Update File: m.py", "@@", " msg = “UTF‑8 only”", "-flag = 1", "+flag = 2"))
    # The context line is the file's own, trailing space included; only the - line changed.
    assert path.read_text(encoding="utf-8") == 'msg = "UTF-8 only" \nflag = 2\n'
    E.edit_file(path, edits=[{"old": "msg = “UTF‑8 only”", "new": 'msg = "ASCII"'}])
    assert path.read_text(encoding="utf-8") == 'msg = "ASCII"\nflag = 2\n'


def test_patch_detection() -> None:
    assert P.looks_like_patch(UPDATE) and P.looks_like_patch("*** Update File: a.py\n@@\n-a\n+b")
    assert P.looks_like_patch("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n")
    assert not P.looks_like_patch('{"path": "a.py"}') and not P.looks_like_patch("--- a list")
    with pytest.raises(P.PatchError, match="starts with"):
        P.parse("hello")
    with pytest.raises(P.PatchError, match="start with a space, - or \\+"):
        P.parse("*** Begin Patch\n*** Update File: a.py\n@@\nnot a hunk line\n*** End Patch")
