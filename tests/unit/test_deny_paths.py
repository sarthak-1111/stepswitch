"""The privacy deny list (`privacy.deny_paths`, SPEC §15, `stepswitch.context.deny`) and how
each agent gets it: the built-in tools, the ACP client, Claude Code and OpenCode rules, and the
Pi and Oh My Pi extensions (whose matcher must agree with this one)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from stepswitch.config import Config
from stepswitch.context.deny import DenyList
from stepswitch.workers.acp import AcpError, decide
from stepswitch.workers.harness import HarnessSettings, HarnessTask, get_adapter, harness_settings
from tests.unit.test_harness_contract import endpoint

REPO = Path(__file__).resolve().parents[2]
NODE = shutil.which("node")
PATTERNS = ["data/**", "*.pem", "/secrets.toml", "private/"]


@pytest.mark.parametrize(
    ("rel", "is_dir", "denied"),
    [
        ("data", True, True),
        ("data/train.csv", False, True),
        ("data/raw/a.csv", False, True),
        ("metadata/notes.md", False, False),
        ("src/data.py", False, False),
        ("keys/server.pem", False, True),
        ("server.pem", False, True),
        ("secrets.toml", False, True),
        ("config/secrets.toml", False, False),
        ("private", True, True),
        ("private/key.txt", False, True),
        ("private", False, False),  # "private/" names directories only
        ("src/app.py", False, False),
    ],
)
def test_patterns_follow_gitignore_rules(rel: str, is_dir: bool, denied: bool) -> None:
    assert DenyList(PATTERNS).denied(rel, is_dir=is_dir) is denied


def test_paths_are_checked_inside_the_root_only(tmp_path: Path) -> None:
    deny = DenyList(PATTERNS)
    (tmp_path / "data").mkdir()
    assert deny.denied_path(tmp_path / "data", tmp_path)
    assert deny.denied_path(Path("data/x.csv"), tmp_path)
    assert not deny.denied_path(tmp_path, tmp_path)
    assert not deny.denied_path(tmp_path.parent / "data" / "x.csv", tmp_path)
    assert not DenyList([])
    assert not DenyList([]).denied_path(tmp_path / "data", tmp_path)


def _config(deny: list[str]) -> Config:
    return Config.model_validate({"privacy": {"deny_paths": deny}})


def test_harness_settings_carry_the_deny_list() -> None:
    settings, _, _ = harness_settings(_config(PATTERNS), "claude-code")
    assert tuple(settings.deny_paths) == tuple(PATTERNS)
    settings, _, _ = harness_settings(_config([]), "claude-code")
    assert tuple(settings.deny_paths) == ()


def test_acp_permission_requests_for_denied_paths_are_refused(tmp_path: Path) -> None:
    settings = HarnessSettings(permission="all", deny_paths=PATTERNS)
    denied = {"kind": "read", "locations": [{"path": str(tmp_path / "data" / "x.csv")}]}
    allowed, why = decide(denied, tmp_path, settings)
    assert not allowed and "privacy deny list" in why
    fine = {"kind": "edit", "locations": [{"path": str(tmp_path / "src" / "app.py")}]}
    assert decide(fine, tmp_path, settings)[0]
    assert decide(fine, tmp_path, HarnessSettings(deny_paths=PATTERNS))[0]


def test_acp_file_methods_refuse_denied_paths(tmp_path: Path) -> None:
    from stepswitch.workers.acp import AcpRun

    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "x.csv").write_text("1,2\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("print(1)\n", encoding="utf-8")
    run = AcpRun.__new__(AcpRun)  # only the file methods: no agent process
    run.workdir = tmp_path
    run.settings = HarnessSettings(deny_paths=PATTERNS)
    with pytest.raises(AcpError, match="privacy deny list"):
        run._read_file({"path": str(tmp_path / "data" / "x.csv")})
    with pytest.raises(AcpError, match="privacy deny list"):
        run._write_file({"path": str(tmp_path / "data" / "new.csv"), "content": "x"})
    assert not (tmp_path / "data" / "new.csv").exists()
    assert run._read_file({"path": str(tmp_path / "app.py")}) == {"content": "print(1)\n"}


def test_claude_code_gets_read_and_edit_deny_rules(tmp_path: Path) -> None:
    adapter = get_adapter("claude-code")
    for permission in ("edits", "all"):
        settings = HarnessSettings(permission=permission, deny_paths=["data/**", "*.pem"])
        args = adapter.command(HarnessTask(prompt="fix it"), tmp_path, endpoint(), settings)
        disallowed = args[args.index("--disallowedTools") + 1 :]
        for rule in ("Read(data/**)", "Edit(data/**)", "Read(*.pem)", "Edit(*.pem)"):
            assert rule in disallowed
    args = adapter.command(HarnessTask(prompt="x"), tmp_path, endpoint(), HarnessSettings())
    assert not any(a.startswith(("Read(", "Edit(")) for a in args)


def test_opencode_gets_read_and_edit_deny_maps() -> None:
    from stepswitch.workers.opencode import OpenCodeAdapter

    adapter = OpenCodeAdapter()
    plain = adapter.config(endpoint(), HarnessSettings())["permission"]
    assert plain["edit"] == "allow" and "read" not in plain
    permission = adapter.config(endpoint(), HarnessSettings(deny_paths=PATTERNS))["permission"]
    for key in ("read", "edit"):
        rules = permission[key]
        assert next(iter(rules)) == "*" and rules["*"] == "allow"  # the catch-all comes first
        for pattern in ("data/*", "*/data/*", "*.pem", "secrets.toml", "private/*"):
            assert rules[pattern] == "deny", (key, pattern)
    assert permission["read"]["*.env"] == "deny"  # the harness's own default is kept


def test_pi_and_oh_my_pi_get_the_compiled_rules(tmp_path: Path) -> None:
    settings = HarnessSettings(deny_paths=PATTERNS, state_dir=tmp_path / "state")
    for name in ("pi", "oh-my-pi"):
        env = get_adapter(name).environment(HarnessTask(prompt="x"), tmp_path, endpoint(), settings)
        rules = json.loads(env["STEPSWITCH_DENY_RULES"])
        assert rules == DenyList(PATTERNS).extension_rules()
    env = get_adapter("pi").environment(
        HarnessTask(prompt="x"), tmp_path, endpoint(), HarnessSettings(state_dir=tmp_path / "s")
    )
    assert "STEPSWITCH_DENY_RULES" not in env


@pytest.mark.skipif(NODE is None, reason="Node.js is not installed")
def test_the_extension_matcher_agrees_with_this_one(tmp_path: Path) -> None:
    for directory in ("data/raw", "private", "src", "keys", "config"):
        (tmp_path / directory).mkdir(parents=True)
    deny = DenyList(PATTERNS)
    paths = [
        "data",
        "data/raw",
        "data/raw/a.csv",
        "metadata/x",
        "src/data.py",
        "keys/server.pem",
        "secrets.toml",
        "config/secrets.toml",
        "private",
        "private/key.txt",
        "src/app.py",
        str(tmp_path / "data" / "raw" / "b.csv"),
        str(tmp_path.parent / "data" / "c.csv"),
    ]
    assert NODE is not None
    proc = subprocess.run(
        [NODE, str(REPO / "adapters" / "test" / "deny_check.js"), *paths],
        cwd=tmp_path,
        env={"STEPSWITCH_DENY_RULES": json.dumps(deny.extension_rules()), "PATH": ""},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    expected = [deny.denied_path(Path(p), tmp_path) for p in paths]
    assert json.loads(proc.stdout) == expected
    assert expected.count(True) == 8
