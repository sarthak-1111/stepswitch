"""Context services work with networking disabled (SPEC §15): the built-in agent's tools (read,
grep, the repository map with its tree-sitter grammars, keyword search, edits, the
language-server client), token counts from a local tokenizer file and the prefetch check, with
every socket connection, datagram and name lookup in this process refused. (Language servers and
ripgrep run as child processes; OS-level isolation covers those on Linux CI, C-006.)"""

from __future__ import annotations

import re
import socket
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from stepswitch import prefetch
from stepswitch.context import lsp, tokens
from stepswitch.workers import tools as T

FAKE_LSP = Path(__file__).parent / "fixtures" / "context" / "fake_lsp.py"
FILES = {
    "pkg/calc.py": "def add(a, b):\n    return a - b\n\n\nclass Calculator:\n    total = 0\n",
    "web/app.ts": "export interface Shape { area(): number }\nexport function draw(s: Shape) {}\n",
    "cmd/main.go": "package main\n\ntype Server struct{}\n\nfunc (s *Server) Start() {}\n",
    "src/lib.rs": "pub struct Engine;\n\nimpl Engine {\n    pub fn run(&self) {}\n}\n",
    "README.md": "A calculator with a total.\n",
}
ANCHOR = re.compile(r"^(\d+:[a-z]{2})\|(.*)$")


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Refuse every connect, send and name lookup in this process; record the attempts."""
    attempts: list[str] = []

    def refuse(what: str) -> Callable[..., Any]:
        def blocked(*args: Any, **kwargs: Any) -> Any:
            attempts.append(what)
            raise OSError(f"networking is disabled in this test ({what})")

        return blocked

    for name in ("connect", "connect_ex", "sendto", "sendmsg"):
        if hasattr(socket.socket, name):
            monkeypatch.setattr(socket.socket, name, refuse(f"socket.{name}"))
    for name in ("getaddrinfo", "gethostbyname", "gethostbyname_ex", "create_connection"):
        monkeypatch.setattr(socket, name, refuse(f"socket.{name}"))
    return attempts


@pytest.fixture
def root(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    for rel, text in FILES.items():
        (project / rel).parent.mkdir(parents=True, exist_ok=True)
        (project / rel).write_text(text, encoding="utf-8")
    return project


def test_the_agent_tools_work_without_a_network(root: Path, no_network: list[str]) -> None:
    spec = lsp.load_specs(
        {"lsp": {"python": {"command": [sys.executable, str(FAKE_LSP)]}}}, registry_dirs=[]
    )["python"]
    manager = lsp.LspManager(root, specs={"python": spec}, request_timeout=5, diagnostics_wait=3)
    config = SimpleNamespace(privacy=SimpleNamespace(deny_paths=[]))
    box = T.Toolbox(root, config=config, offline=True, lsp_manager=manager)
    try:
        read = box.execute("read", {"path": "pkg/calc.py"})
        assert not read.is_error, read.text
        grep = box.execute("grep", {"pattern": "def add"})
        assert "pkg/calc.py" in grep.text, grep.text
        repo_map = box.execute("repo_map", {})
        assert not repo_map.is_error, repo_map.text
        for name in ("Calculator", "Shape", "Server", "Engine"):  # Python, TS, Go, Rust
            assert name in repo_map.text, (name, repo_map.text)
        found = box.execute("search", {"query": "calculator total"})
        assert not found.is_error and "README.md" in found.text, found.text
        rows = {}
        for row in read.text.split("\n")[1:]:
            match = ANCHOR.match(row)
            if match:
                rows[match.group(2)] = match.group(1)
        edit = {"op": "replace", "start": rows["    return a - b"], "text": "    return a + b"}
        done = box.execute("edit", {"path": "pkg/calc.py", "edits": [edit]})
        assert not done.is_error and done.changed_files == ["pkg/calc.py"], done.text
        defs = box.execute("lsp", {"action": "definition", "symbol": "Calculator"})
        assert "pkg/calc.py:5" in defs.text, defs.text
    finally:
        box.close()
    assert no_network == []


@pytest.mark.skipif(not tokens.tokenizers_available(), reason="the tokens extra is not installed")
def test_token_counts_from_a_local_file_without_a_network(
    tmp_path: Path, no_network: list[str]
) -> None:
    from tokenizers import Tokenizer, models, pre_tokenizers

    vocab = {"[UNK]": 0, "fix": 1, "the": 2, "bug": 3}
    tokenizer = Tokenizer(models.WordLevel(vocab, unk_token="[UNK]"))
    tokenizer.pre_tokenizer = pre_tokenizers.Whitespace()
    path = tmp_path / "tokenizer.json"
    tokenizer.save(str(path))
    count = tokens.count("fix the bug now", tokens.FileCounter(path, "tiny"))
    assert count.exact and count.tokens == 4 and count.tokenizer == "tiny"
    assert no_network == []


def test_the_prefetch_check_opens_no_connection(tmp_path: Path, no_network: list[str]) -> None:
    items = prefetch.check(SimpleNamespace(model_extra={}), tmp_path / "models")
    assert {item.service for item in items} >= {"tree-sitter", "tokenizers", "search"}
    assert no_network == []


def test_the_guard_refuses_connections_and_lookups(no_network: list[str]) -> None:
    with pytest.raises(OSError, match="networking is disabled"):
        socket.create_connection(("127.0.0.1", 9))
    with pytest.raises(OSError, match="networking is disabled"):
        socket.getaddrinfo("models.example.org", 443)
    with socket.socket() as sock, pytest.raises(OSError, match="networking is disabled"):
        sock.connect(("192.0.2.1", 80))
    assert no_network == ["socket.create_connection", "socket.getaddrinfo", "socket.connect"]
