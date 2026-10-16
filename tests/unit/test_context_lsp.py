"""The LSP client against a tiny fake language server (tests/unit/fixtures/context/fake_lsp.py)."""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from stepswitch.config import Config
from stepswitch.context import lsp

FAKE_SERVER = Path(__file__).parent / "fixtures" / "context" / "fake_lsp.py"
SOURCE = """\
class Greeter:
    def greet(self, name):
        return "hi " + name  # TODO polish


def helper():
    g = Greeter()
    return g.greet("x") + "😀" + sleepy() + report()
"""


def fake_spec(**extra: object) -> lsp.ServerSpec:
    data = {"command": [sys.executable, str(FAKE_SERVER)], "settings": {"fake": {"level": 3}}}
    data.update(extra)
    return lsp.load_specs({"lsp": {"python": data}}, registry_dirs=[])["python"]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text(SOURCE, encoding="utf-8")
    return tmp_path


@pytest.fixture
def manager(project: Path) -> Iterator[lsp.LspManager]:
    mgr = lsp.LspManager(
        project, specs={"python": fake_spec()}, request_timeout=5.0, diagnostics_wait=3.0
    )
    yield mgr
    mgr.close()


def test_specs_come_from_registry_files_then_config(tmp_path: Path) -> None:
    registry = tmp_path / "registry" / "lsp"
    registry.mkdir(parents=True)
    (registry / "go.toml").write_text(
        'language = "go"\nextensions = ["go"]\ncommand = ["gopls"]\nroot_markers = ["go.mod"]\n',
        encoding="utf-8",
    )
    (registry / "python.toml").write_text(
        'language = "python"\ncommand = ["registry-server", "--stdio"]\n', encoding="utf-8"
    )
    config = Config.model_validate({"lsp": {"python": {"command": "configured-server --stdio"}}})
    specs = lsp.load_specs(config, registry_dirs=[tmp_path / "registry"])
    assert specs["go"].command == ("gopls",)
    assert specs["go"].extensions == (".go",)
    assert specs["go"].root_markers == ("go.mod",)
    assert specs["python"].command == ("configured-server", "--stdio")  # config wins
    assert specs["python"].extensions == (".py", ".pyi")
    assert specs["python"].source == "config"


def test_specs_without_a_command_are_dropped() -> None:
    assert lsp.load_specs({"lsp": {"rust": {"extensions": ["rs"]}}}, registry_dirs=[]) == {}


def test_utf16_positions_round_trip() -> None:
    text = 'x = "😀" + name'
    column = text.index("name") + 1
    character = lsp.to_lsp_character(text, column)
    assert character == column  # the emoji is two UTF-16 units: one more than its one char
    assert lsp.from_lsp_character(text, character) == column
    assert lsp.to_lsp_character(text, column, "utf-8") == len(text[: column - 1].encode())
    assert lsp.to_lsp_character(text, column, "utf-32") == column - 1
    assert lsp.from_lsp_character(text, column - 1, "utf-32") == column


def test_uri_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "a b" / "mod.py"
    uri = lsp.path_to_uri(path)
    assert uri.startswith("file://")
    back = lsp.uri_to_path(uri)
    assert back is not None and back.resolve() == path.resolve()
    assert lsp.uri_to_path("untitled:Untitled-1") is None


def test_hover_text_shapes() -> None:
    assert lsp.hover_text({"kind": "markdown", "value": " **x** "}) == "**x**"
    assert lsp.hover_text(["a", {"language": "python", "value": "b"}]) == "a\n\nb"
    assert lsp.hover_text(None) == ""


def test_definition_references_and_hover(manager: lsp.LspManager, project: Path) -> None:
    mod = project / "pkg" / "mod.py"
    # line 7: "    g = Greeter()" -> definition of Greeter is on line 1
    found = manager.definition(mod, 7, 9)
    assert [(loc.path, loc.line, loc.column) for loc in found] == [("pkg/mod.py", 1, 7)]
    refs = manager.references(mod, 1, 7)
    assert [(loc.line, loc.column) for loc in refs] == [(1, 7), (7, 9)]
    # line 8 has an emoji before `sleepy`; the column must survive the UTF-16 conversion
    line8 = SOURCE.split("\n")[7]
    greet_column = line8.index("greet") + 1
    assert manager.hover(mod, 8, greet_column) == "**greet**"


def test_server_requests_are_answered(manager: lsp.LspManager, project: Path) -> None:
    mod = project / "pkg" / "mod.py"
    line8 = SOURCE.split("\n")[7]
    manager.definition(mod, 1, 7)  # starts the server; it then asks us two questions
    deadline = time.monotonic() + 5
    report: dict[str, object] = {}
    while time.monotonic() < deadline:
        report = json.loads(manager.hover(mod, 8, line8.index("report") + 1))
        if len(report) == 2:
            break
        time.sleep(0.05)
    assert report["cfg"] == [3, None]  # settings looked up by section, null when missing
    assert isinstance(report["odd"], dict) and report["odd"]["code"] == -32601


def test_diagnostics_follow_edits(manager: lsp.LspManager, project: Path) -> None:
    mod = project / "pkg" / "mod.py"
    first = manager.diagnostics(mod)
    assert [(d.line, d.severity, d.source, d.code) for d in first] == [(3, "warning", "fake", "T1")]
    assert first[0].column == SOURCE.split("\n")[2].index("TODO") + 1
    mod.write_text(SOURCE.replace("  # TODO polish", ""), encoding="utf-8")
    assert manager.diagnostics(mod) == []


def test_unanswered_request_times_out(project: Path) -> None:
    mgr = lsp.LspManager(project, specs={"python": fake_spec()}, request_timeout=0.5)
    try:
        mod = project / "pkg" / "mod.py"
        line8 = SOURCE.split("\n")[7]
        with pytest.raises(lsp.LspTimeout, match="did not answer textDocument/hover"):
            mgr.hover(mod, 8, line8.index("sleepy") + 1)
        assert mgr.hover(mod, 8, line8.index("greet") + 1) == "**greet**"  # still usable
    finally:
        mgr.close()


def test_close_shuts_the_server_down(project: Path) -> None:
    mgr = lsp.LspManager(project, specs={"python": fake_spec()})
    mod = project / "pkg" / "mod.py"
    client = mgr.client_for(mod)
    client.start()
    connection = client._conn
    assert connection is not None and connection.alive()
    mgr.close()
    assert not connection.alive()
    assert connection._proc.returncode == 0  # shutdown then exit: a clean exit


def test_missing_server_and_unconfigured_type_are_clear_errors(project: Path) -> None:
    spec = lsp.ServerSpec("python", ("no-such-language-server-xyz",), (".py",))
    mgr = lsp.LspManager(project, specs={"python": spec})
    with pytest.raises(lsp.LspError, match="not installed"):
        mgr.definition(project / "pkg" / "mod.py", 1, 1)
    (project / "x.rs").write_text("fn main() {}\n", encoding="utf-8")
    with pytest.raises(lsp.LspNotConfigured, match=r"no language server is configured for \.rs"):
        mgr.definition(project / "x.rs", 1, 1)
    mgr.close()


def test_one_server_per_marked_project_root(project: Path) -> None:
    (project / "sub").mkdir()
    (project / "sub" / "pyproject.toml").write_text("", encoding="utf-8")
    (project / "sub" / "a.py").write_text("x = 1\n", encoding="utf-8")
    mgr = lsp.LspManager(project, specs={"python": fake_spec(root_markers=["pyproject.toml"])})
    top = mgr.client_for(project / "pkg" / "mod.py")
    sub = mgr.client_for(project / "sub" / "a.py")
    assert top.root == project.resolve()
    assert sub.root == (project / "sub").resolve()
    assert mgr.client_for(project / "sub" / "a.py") is sub
    mgr.close()
