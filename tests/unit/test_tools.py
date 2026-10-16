"""The built-in agent's toolbox (src/stepswitch/workers/tools.py)."""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import jsonschema
import pytest
from jsonschema_specifications import REGISTRY

from stepswitch.context import lsp
from stepswitch.workers import tools as T

FAKE_SERVER = Path(__file__).parent / "fixtures" / "context" / "fake_lsp.py"
CALC = "def add(a, b):\n    return a - b\n\n\nclass Calculator:\n    total = 0  # TODO reset\n"
ANCHOR_ROW = re.compile(r"^(\d+:[a-z]{2})\|(.*)$")


@pytest.fixture
def root(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    (project / "pkg").mkdir(parents=True)
    (project / "pkg" / "calc.py").write_text(CALC, encoding="utf-8")
    (project / "README.md").write_text("Calculator project.\n", encoding="utf-8")
    (project / "secrets").mkdir()
    (project / "secrets" / "token.txt").write_text("TOKEN=abc\n", encoding="utf-8")
    (project / ".git").mkdir()
    (project / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    return project


@pytest.fixture
def box(root: Path) -> T.Toolbox:
    config = SimpleNamespace(privacy=SimpleNamespace(deny_paths=["secrets/**", "*.pem"]))
    return T.Toolbox(root, config=config, max_output_chars=4000)


def anchors(output: T.ToolOutput) -> dict[str, str]:
    """{line text: anchor} from a read result."""
    found = {}
    for row in output.text.split("\n")[1:]:
        match = ANCHOR_ROW.match(row)
        if match:
            found[match.group(2)] = match.group(1)
    return found


# -- specs ---------------------------------------------------------------------------------


def test_specs_cover_every_tool_and_stay_small(box: T.Toolbox) -> None:
    specs = box.specs()
    assert [s.name for s in specs] == [*T.TOOL_NAMES, *T.CONTROL_TOOLS]
    wire = [s.model_dump(exclude={"examples"}, exclude_none=True) for s in specs]
    assert len(json.dumps(wire)) < 4800  # about 1,200 tokens for the whole toolset
    for spec in specs:
        assert spec.description and len(spec.description) < 120
        assert spec.parameters["type"] == "object"
    assert [s.name for s in box.specs(["read", "finish"])] == ["read", "finish"]


def _no_extra_fields(schema: Any) -> Any:
    if isinstance(schema, dict):
        strict = {key: _no_extra_fields(value) for key, value in schema.items()}
        if "properties" in strict:
            strict["additionalProperties"] = False
        return strict
    if isinstance(schema, list):
        return [_no_extra_fields(value) for value in schema]
    return schema


def test_every_spec_has_one_example_that_fits_its_schema(box: T.Toolbox) -> None:
    for spec in box.specs():
        assert spec.examples is not None and len(spec.examples) == 1, spec.name
        example = spec.examples[0]
        assert "$ref" not in json.dumps(spec.parameters)  # nothing could be fetched
        jsonschema.Draft202012Validator.check_schema(spec.parameters)
        # Only declared fields, so the example never teaches a field the tool ignores.
        strict = _no_extra_fields(spec.parameters)
        jsonschema.Draft202012Validator(strict, registry=REGISTRY).validate(example)
        box._arguments(spec.name, example)  # and the toolbox reads it as given
    edit = next(s for s in box.specs() if s.name == "edit")
    assert edit.examples is not None
    for change in edit.examples[0]["edits"]:
        for key in ("start", "end"):
            assert re.fullmatch(r"\d+:[a-z]{2}", change.get(key, "1:ab"))  # the read format


# -- read and edit -------------------------------------------------------------------------


def test_read_shows_anchors_that_edit_accepts(box: T.Toolbox, root: Path) -> None:
    shown = box.execute("read", {"path": "pkg/calc.py"})
    assert not shown.is_error
    assert shown.text.startswith("pkg/calc.py lines 1-6 of 6")
    rows = anchors(shown)
    assert set(rows) >= {"def add(a, b):", "    return a - b"}
    assert shown.data["total_lines"] == 6 and len(shown.data["sha256"]) == 64

    edit = {"op": "replace", "start": rows["    return a - b"], "text": "    return a + b"}
    done = box.execute("edit", {"path": "pkg/calc.py", "edits": [edit]})
    assert not done.is_error, done.text
    assert done.text.startswith("edited pkg/calc.py: +1 -1")
    assert "updated lines with fresh anchors:" in done.text
    assert done.changed_files == ["pkg/calc.py"]
    assert "-    return a - b" in done.data["diff"]
    assert "return a + b" in (root / "pkg" / "calc.py").read_text(encoding="utf-8")

    stale = box.execute("edit", {"path": "pkg/calc.py", "edits": [edit]})
    assert stale.is_error and stale.text.startswith("edit refused: stale anchor")
    assert stale.changed_files == []


def test_edit_creates_files_and_reports_no_change(box: T.Toolbox, root: Path) -> None:
    made = box.execute("edit", {"path": "pkg/new.py", "text": "x = 1", "create": True})
    assert made.text.startswith("created pkg/new.py (1 lines)")
    assert (root / "pkg" / "new.py").read_text(encoding="utf-8") == "x = 1\n"
    same = box.execute("edit", {"path": "pkg/new.py", "text": "x = 1\n"})
    assert same.text.startswith("no change") and same.changed_files == []


def test_read_ranges_directories_and_refusals(root: Path) -> None:
    (root / "long.txt").write_text("".join(f"line {i}\n" for i in range(1, 301)), encoding="utf-8")
    (root / "blob.bin").write_bytes(b"\x00\x01\x02")
    (root / "empty.txt").write_text("", encoding="utf-8")
    box = T.Toolbox(root, max_output_chars=800)
    part = box.execute("read", {"path": "long.txt", "start": 10, "end": 12})
    assert part.text.split("\n")[0] == "long.txt lines 10-12 of 300"
    assert len(anchors(part)) == 3
    capped = box.execute("read", {"path": "long.txt"})
    assert "[more: read with start=" in capped.text and len(capped.text) <= 800
    listing = box.execute("read", {"path": "."})
    assert listing.text.startswith("./ (")
    assert "pkg/" in listing.text and ".git" not in listing.text
    assert box.execute("read", {"path": "blob.bin"}).text == "blob.bin is a binary file (3 bytes)"
    assert box.execute("read", {"path": "empty.txt"}).text == "empty.txt is empty (0 lines)"
    past = box.execute("read", {"path": "long.txt", "start": 999})
    assert past.is_error and "past the end" in past.text


def test_short_files_are_always_shown_whole(box: T.Toolbox, root: Path) -> None:
    lines = T.READ_WHOLE_LINES
    (root / "short.py").write_text("".join(f"x{i} = {i}\n" for i in range(lines)), "utf-8")
    (root / "long.py").write_text("".join(f"x{i} = {i}\n" for i in range(lines + 1)), "utf-8")
    short = box.execute("read", {"path": "short.py", "start": 10, "end": 12})
    assert short.text.split("\n")[0] == (
        f"short.py lines 1-{lines} of {lines} (the whole file: short files are shown whole)"
    )
    assert len(anchors(short)) == lines
    assert short.data["requested"] == [10, 12]
    whole = box.execute("read", {"path": "short.py"})  # no range asked: no note
    assert whole.text.split("\n")[0] == f"short.py lines 1-{lines} of {lines}"
    part = box.execute("read", {"path": "long.py", "start": 10, "end": 12})
    assert part.text.split("\n")[0] == f"long.py lines 10-12 of {lines + 1}"


def test_paths_stay_inside_the_root(box: T.Toolbox, root: Path) -> None:
    absolute = box.execute("read", {"path": str(root / "pkg" / "calc.py")})
    assert absolute.is_error and "Use `pkg/calc.py`" in absolute.text
    outside = box.execute("read", {"path": "../elsewhere.txt"})
    assert outside.is_error and "outside the repository" in outside.text
    internal = box.execute("read", {"path": ".git/config"})
    assert internal.is_error and "internal state" in internal.text
    denied = box.execute("read", {"path": "secrets/token.txt"})
    assert denied.is_error and "privacy deny list" in denied.text
    assert box.execute("edit", {"path": "x.pem", "text": "k", "create": True}).is_error
    missing = box.execute("read", {"path": "calc.py"})
    assert missing.is_error and "Did you mean: pkg/calc.py?" in missing.text
    grep = box.execute("grep", {"pattern": "TOKEN"})
    assert grep.data["matches"] == 0  # denied files never show up in results
    assert "secrets" not in box.execute("read", {"path": "."}).text
    folder = box.execute("read", {"path": "secrets"})
    assert folder.is_error and "privacy deny list" in folder.text


def test_symbolic_links_cannot_escape(box: T.Toolbox, root: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("private\n", encoding="utf-8")
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are not available here")
    escaped = box.execute("read", {"path": "link/private.txt"})
    assert escaped.is_error and "outside the repository" in escaped.text


# -- forgiving arguments -------------------------------------------------------------------


def test_arguments_are_read_forgivingly(box: T.Toolbox) -> None:
    aliased = box.execute("read_file", {"file_path": "pkg/calc.py", "start_line": "2", "to": 2})
    assert aliased.text.startswith("pkg/calc.py lines 1-6 of 6")  # short: shown whole
    assert aliased.data["requested"] == [2, 2]
    assert aliased.data["tool"] == "read"
    assert not box.execute("read", '{"path": "pkg/calc.py"}').is_error
    assert not box.execute("read", '```json\n{"path": "pkg/calc.py"}\n```').is_error
    assert not box.execute("read", "pkg/calc.py").is_error  # one required argument
    assert box.execute("rg", "def add").data["matches"] == 1


def test_bad_calls_get_one_helpful_paragraph(box: T.Toolbox) -> None:
    unknown = box.execute("frobnicate", {})
    assert unknown.is_error and "Available tools: read, grep" in unknown.text
    broken = box.execute("read", '{"path": ')
    assert broken.is_error and "not valid JSON" in broken.text and '"path"' in broken.text
    missing = box.execute("grep", {})
    assert missing.is_error and missing.text.startswith("grep: missing `pattern`. Example: {")
    wrong = box.execute("read", {"path": "pkg/calc.py", "start": "first"})
    assert wrong.is_error and "`start` must be a number" in wrong.text
    not_object = box.execute("grep", "[1, 2]")
    assert not_object.is_error and "must be a JSON object" in not_object.text


# -- grep, repo_map, search, lsp -----------------------------------------------------------


def test_grep_repo_map_and_search(box: T.Toolbox) -> None:
    grep = box.execute("grep", {"pattern": "class \\w+", "glob": "*.py"})
    assert grep.text.split("\n")[1] == "pkg/calc.py:5: class Calculator:"
    assert grep.data["files"] == ["pkg/calc.py"]
    literal = box.execute("grep", {"pattern": "add(a"})  # not a valid regex: plain text
    assert literal.data["matches"] == 1 and "searched as plain text" in literal.text

    outline = box.execute("repo_map", {"focus": ["pkg/calc.py"]})
    assert outline.text.startswith("repo map: ")
    assert "pkg/calc.py:" in outline.text and "def add(a, b)" in outline.text
    assert outline.data["estimated"] is True

    found = box.execute("search", {"query": "calculator total"})
    assert "keyword" in found.text and found.data["hits"]
    assert found.data["hits"][0].startswith("pkg/calc.py:")


class _RemoteEmbedder:
    local = False

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise AssertionError("an offline run must not call a remote embedder")


def test_offline_search_skips_a_remote_embedder(root: Path) -> None:
    box = T.Toolbox(root, embedder=_RemoteEmbedder(), offline=True)
    found = box.execute("search", {"query": "add numbers"})
    assert not found.is_error
    assert "remote and offline mode is on; keyword-only" in found.text


def test_lsp_tool_with_a_fake_server(root: Path) -> None:
    spec = lsp.load_specs(
        {"lsp": {"python": {"command": [sys.executable, str(FAKE_SERVER)]}}}, registry_dirs=[]
    )["python"]
    manager = lsp.LspManager(root, specs={"python": spec}, request_timeout=5, diagnostics_wait=3)
    box = T.Toolbox(root, lsp_manager=manager)
    try:
        defs = box.execute("lsp", {"action": "definition", "symbol": "Calculator"})
        assert defs.text == "1 locations\npkg/calc.py:5:7: class Calculator:"
        diagnostics = box.execute("lsp", {"action": "diagnostics", "path": "pkg/calc.py"})
        assert diagnostics.text.startswith("1 diagnostics in pkg/calc.py")
        column = CALC.split("\n")[5].index("TODO") + 1
        expected = f"pkg/calc.py:6:{column}: warning: todo left in code [fake T1]"
        assert expected in diagnostics.text
        hover = box.execute("lsp", {"action": "hover", "path": "pkg/calc.py", "line": 1})
        assert hover.text == "**def**"  # column defaults to the first non-blank character
        (root / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
        missing = box.execute("lsp", {"action": "defs", "path": "main.rs", "line": 1})
        assert missing.is_error and "no language server is configured for .rs" in missing.text
        bad = box.execute("lsp", {"action": "rename", "path": "pkg/calc.py"})
        assert bad.is_error and "must be one of" in bad.text
    finally:
        box.close()


# -- run and test --------------------------------------------------------------------------


def test_run_uses_a_minimal_environment(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_API_KEY", "s3cret-value")
    box = T.Toolbox(root, env={"EXTRA_SETTING": "on"})
    code = "import os; print(sorted(os.environ)); print(os.getcwd())"
    done = box.execute("run", {"command": [sys.executable, "-c", code]})
    assert not done.is_error and done.data["exit_code"] == 0
    assert "MY_API_KEY" not in done.text and "s3cret" not in done.text
    assert "'EXTRA_SETTING'" in done.text and "'PATH'" in done.text
    assert Path(done.text.rstrip().split("\n")[-1]).resolve() == root.resolve()
    failing = box.execute("run", {"command": [sys.executable, "-c", "raise SystemExit(3)"]})
    assert not failing.is_error and failing.data["exit_code"] == 3  # an exit code, not an error


def test_run_timeout_stops_the_whole_process_tree(root: Path) -> None:
    marker = root / "late.txt"
    child = f"import time; time.sleep(1.5); open({str(marker)!r}, 'w').close()"
    parent = (
        "import subprocess, sys, time; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); time.sleep(30)"
    )
    box = T.Toolbox(root, run_timeout=1.0)
    started = time.monotonic()
    done = box.execute("run", {"command": [sys.executable, "-c", parent], "timeout": 60})
    assert done.data["timed_out"] is True and "timed out after 1 s" in done.text
    assert time.monotonic() - started < 10
    time.sleep(max(0.0, 2.5 - (time.monotonic() - started)))
    assert not marker.exists()  # the grandchild was stopped with its parent


def test_run_refusals(root: Path) -> None:
    assert "disabled" in T.Toolbox(root, allow_run=False).execute("run", "python -V").text
    box = T.Toolbox(root)
    unknown = box.execute("run", {"command": "no-such-program-xyz --help"})
    assert unknown.is_error and "was not found on PATH" in unknown.text
    assert box.execute("test", {}).text.startswith("no test command is configured")


def test_test_tool_summarizes_a_real_pytest_run(root: Path) -> None:
    tests = root / "tests"
    tests.mkdir()
    (tests / "test_calc.py").write_text(
        "import sys\nsys.path.insert(0, 'pkg')\nfrom calc import add\n\n\n"
        "def test_add():\n    assert add(1, 2) == 3\n\n\n"
        "def test_zero():\n    assert add(0, 0) == 0\n",
        encoding="utf-8",
    )
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    box = T.Toolbox(root, test_command=command)
    done = box.execute("test", {})
    assert not done.is_error, done.text
    assert done.text.startswith("tests failed (exit 1")
    assert "1 passed, 1 failed" in done.text.split("\n")[0]
    assert "  tests/test_calc.py::test_add" in done.text
    assert done.data["counts"] == {"passed": 1, "failed": 1, "errors": 0, "skipped": 0}
    assert done.data["ok"] is False and done.data["runner"] == "pytest"
    assert any("assert -1 == 3" in row for row in done.text.split("\n"))


def test_test_tool_passes_the_selector(root: Path) -> None:
    runner = root / "runner.py"
    runner.write_text("import sys\nprint(sys.argv[1:])\nprint('3 passed in 0.01s')\n")
    box = T.Toolbox(root, test_command=[sys.executable, str(runner), "-q"])
    done = box.execute("test", {"selector": "tests\\test_calc.py::test_add"})
    assert done.text.startswith("tests passed (exit 0") and "3 passed" in done.text
    assert done.data["argv"][1:] == [str(runner), "-q", "tests/test_calc.py::test_add"]
    assert done.data["ok"] is True


# -- helpers -------------------------------------------------------------------------------


def test_finish_and_bound(box: T.Toolbox) -> None:
    done = box.execute("finish", {"summary": "Fixed add."})
    assert done.data == {"finish": True, "summary": "Fixed add.", "tool": "finish"}
    assert box.execute("done", "all good").data["summary"] == "all good"
    text = "".join(f"row {i}\n" for i in range(1000))
    short = T.bound(text, 200)
    assert short.startswith("row 0\n") and short.rstrip().endswith("row 999")
    assert "characters omitted" in short and len(short) < 260
    assert T.bound("small", 200) == "small"


PYTEST_OUTPUT = """\
..F                                                                      [100%]
=================================== FAILURES ===================================
___________________________________ test_add ___________________________________

    def test_add():
>       assert add(1, 2) == 3
E       assert -1 == 3
E        +  where -1 = add(1, 2)

tests/test_calc.py:4: AssertionError
=========================== short test summary info ============================
FAILED tests/test_calc.py::test_add - assert -1 == 3
1 failed, 2 passed in 0.12s
"""
GO_OUTPUT = """\
--- FAIL: TestAdd (0.00s)
    calc_test.go:8: got -1, want 3
FAIL
FAIL\texample.com/calc\t0.002s
"""
CARGO_OUTPUT = """\
running 2 tests
test tests::adds ... FAILED
test tests::subs ... ok

test result: FAILED. 1 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out
"""
JEST_OUTPUT = """\
 FAIL  src/calc.test.js
  ● calc › adds numbers

    expect(received).toBe(expected)

Tests:       1 failed, 3 passed, 4 total
"""


def test_summaries_of_common_test_runners() -> None:
    py = T.summarize_tests(PYTEST_OUTPUT)
    assert (py.runner, py.passed, py.failed, py.errors) == ("pytest", 2, 1, 0)
    assert py.failing == ["tests/test_calc.py::test_add"]
    assert "tests/test_calc.py::test_add: assert -1 == 3" in py.details
    assert "E       assert -1 == 3" in py.details
    assert "tests/test_calc.py:4: AssertionError" in py.details
    go = T.summarize_tests(GO_OUTPUT)
    assert (go.runner, go.failing) == ("go", ["TestAdd"])
    cargo = T.summarize_tests(CARGO_OUTPUT)
    assert (cargo.runner, cargo.passed, cargo.failed, cargo.failing) == (
        "cargo",
        1,
        1,
        ["tests::adds"],
    )
    jest = T.summarize_tests(JEST_OUTPUT)
    assert (jest.runner, jest.passed, jest.failed) == ("jest", 3, 1)
    assert jest.failing == ["calc › adds numbers"]
    assert T.summarize_tests("nothing recognisable").runner == "unknown"
