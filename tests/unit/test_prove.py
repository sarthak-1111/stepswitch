from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import textwrap
from pathlib import Path
from types import ModuleType

import pytest

from stepswitch.prove import claims as claims_module
from stepswitch.prove import render_readme, runner
from stepswitch.prove.digest import source_digest

ROOT = Path(__file__).resolve().parents[2]


def _phase_check() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "phase_check", ROOT / "scripts" / "phase_check.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["phase_check"] = module
    spec.loader.exec_module(module)
    return module


def test_digest_matches_the_phase_check_digest() -> None:
    assert source_digest(ROOT) == _phase_check().source_digest(ROOT)


def test_digest_ignores_status_lines_and_line_endings(tmp_path: Path) -> None:
    (tmp_path / "claims").mkdir()
    (tmp_path / "src").mkdir()
    registry = tmp_path / "claims" / "claims.yaml"
    registry.write_bytes(b"claims:\n  - id: C-001\n    status: pending\n")
    (tmp_path / "src" / "a.py").write_bytes(b"x = 1\n")
    before = source_digest(tmp_path)
    registry.write_bytes(b"claims:\n  - id: C-001\n    status: passing\n")
    (tmp_path / "src" / "a.py").write_bytes(b"x = 1\r\n")
    assert source_digest(tmp_path) == before
    (tmp_path / "src" / "a.py").write_bytes(b"x = 2\n")
    assert source_digest(tmp_path) != before


REGISTRY = {
    "claims": [
        {"id": "C-001", "phase": 1, "tests": ["tests/claims/test_a.py::test_one"]},
        {
            "id": "C-002",
            "phase": 2,
            "parts": [
                {"name": "first", "phase": 2, "tests": ["tests/claims/test_b.py::test_b"]},
                {"name": "second", "phase": 3, "tests": ["tests/claims/test_c.py::test_c"]},
            ],
        },
    ]
}


def test_select_by_phase_claim_and_skip() -> None:
    assert set(runner.select(REGISTRY, phase=1, claims=[], skip=[])) == {"C-001"}
    by_phase_2 = runner.select(REGISTRY, phase=2, claims=[], skip=[])
    assert by_phase_2["C-002"]["all_due"] is False
    assert [p["name"] for p in by_phase_2["C-002"]["parts"]] == ["first"]
    explicit = runner.select(REGISTRY, phase=None, claims=["C-002"], skip=[])
    assert list(explicit) == ["C-002"] and explicit["C-002"]["all_due"]
    assert set(runner.select(REGISTRY, phase=None, claims=[], skip=["C-001"])) == {"C-002"}
    with pytest.raises(ValueError, match="unknown claim"):
        runner.select(REGISTRY, phase=None, claims=["C-999"], skip=[])


@pytest.mark.parametrize(
    ("outcomes", "all_due", "status"),
    [
        (["passed", "passed"], True, "passing"),
        (["passed"], False, "pending"),
        (["passed", "skipped"], True, "pending"),
        (["passed", "failed"], True, "failing"),
        (["error"], True, "failing"),
        ([], True, "pending"),
    ],
)
def test_status_rules(outcomes: list[str], all_due: bool, status: str) -> None:
    assert runner._status(outcomes, all_due) == status


def test_render_section_lists_only_passing_claims_and_passes_the_lint() -> None:
    integrity = importlib.import_module("tests.claims.test_integrity")
    registry = claims_module.load(ROOT)
    for claim in registry["claims"]:
        claim["status"] = "pending"
    assert render_readme.render_claims_section(registry) == (
        f"{render_readme.START}\n{render_readme.EMPTY}\n{render_readme.END}"
    )
    registry["claims"][3]["status"] = "passing"  # C-004
    registry["claims"][6]["status"] = "passing"  # C-007
    section = render_readme.render_claims_section(registry)
    assert section.count("<!-- claim:") == 2
    assert not integrity.lint_public_text(section, registry)
    assert not integrity.lint_readme_numbers(section, registry)


def test_render_section_shows_numbers_from_evidence(tmp_path: Path) -> None:
    integrity = importlib.import_module("tests.claims.test_integrity")
    registry = claims_module.load(ROOT)
    for claim in registry["claims"]:
        claim["status"] = "pending"
    claim = registry["claims"][0]  # C-001
    claim["status"] = "passing"
    claim["readme_numbers"] = [
        {"key": "sections.run.peak_gb", "label": "peak memory", "unit": "GiB", "digits": 2},
        {"key": "sections.run.tokens", "label": "tokens", "digits": 0},
        {"key": "sections.run.missing", "label": "not recorded"},
    ]
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    data = {"sections": {"run": {"peak_gb": 6.428, "tokens": 29840}}}
    (evidence_dir / "C-001.json").write_text(json.dumps(data), encoding="utf-8")
    section = render_readme.render_claims_section(registry, evidence_dir)
    assert "peak memory <!-- num:C-001:sections.run.peak_gb -->6.43<!-- /num --> GiB" in section
    assert "tokens <!-- num:C-001:sections.run.tokens -->29,840<!-- /num -->)" in section
    assert "not recorded" not in section
    assert not integrity.lint_readme_numbers(section, registry, evidence_dir)
    data["sections"]["run"]["peak_gb"] = 7.1
    (evidence_dir / "C-001.json").write_text(json.dumps(data), encoding="utf-8")
    assert integrity.lint_readme_numbers(section, registry, evidence_dir)


def test_results_table_shows_values_of_passing_claims_only(tmp_path: Path) -> None:
    integrity = importlib.import_module("tests.claims.test_integrity")
    registry = claims_module.load(ROOT)
    for claim in registry["claims"]:
        claim["status"] = "pending"
    registry["claims"][0]["status"] = "passing"  # C-001
    registry["results"] = {
        "columns": ["Ours", "Theirs, `model-1-2`"],
        "rows": [
            {
                "label": "Passed",
                "unit": "%",
                "digits": 1,
                "cells": ["C-001:sections.run.rate", "C-002:sections.run.rate"],
            },
            {"label": "Tokens", "cells": ["C-001:sections.run.tokens", "C-001:sections.run.gone"]},
        ],
    }
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    for cid in ("C-001", "C-002"):
        data = {"sections": {"run": {"rate": 95.24, "tokens": 41872}}}
        (evidence_dir / f"{cid}.json").write_text(json.dumps(data), encoding="utf-8")
    table = render_readme.render_results_section(registry, evidence_dir)
    lines = table.splitlines()
    assert lines[0] == render_readme.RESULTS_START and lines[-1] == render_readme.RESULTS_END
    assert lines[1] == "| | Ours | Theirs, `model-1-2` |" and lines[2] == "|---|---|---|"
    dash = render_readme.DASH
    assert lines[3] == (
        f"| Passed | <!-- num:C-001:sections.run.rate -->95.2<!-- /num --> % | {dash} |"
    )
    assert (
        lines[4]
        == f"| Tokens | <!-- num:C-001:sections.run.tokens -->41,872<!-- /num --> | {dash} |"
    )
    assert "[C-001](claims/evidence/C-001.json)" in table and "C-002" not in table
    assert not integrity.lint_readme_numbers(table, registry, evidence_dir)
    assert not integrity.lint_public_text(table, registry)


MINI_TESTS = """
import pytest

pytestmark = [pytest.mark.claim("C-001")]

def test_ok():
    assert True

def test_bad():
    assert False, "broken on purpose"

def test_skip():
    pytest.skip("not applicable here")
"""

MINI_REGISTRY = """\
# comment that must survive
version: 1
claims:
  - id: C-001
    text: "A."
    phase: 1
    tests:
      - tests/claims/test_mini.py::test_ok
    status: pending   # trailing comment
  - id: C-002
    text: "B."
    phase: 1
    tests:
      - tests/claims/test_mini.py::test_bad
    status: pending
  - id: C-003
    text: "C."
    phase: 1
    tests:
      - tests/claims/test_mini.py::test_skip
    status: passing
  - id: C-004
    text: "D."
    phase: 2
    tests:
      - tests/claims/test_missing.py::test_nothing
    status: pending
"""


def _mini_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "tests" / "claims").mkdir(parents=True)
    (root / "claims").mkdir()
    (root / "tests" / "claims" / "test_mini.py").write_text(MINI_TESTS, encoding="utf-8")
    (root / "claims" / "claims.yaml").write_text(MINI_REGISTRY, encoding="utf-8")
    (root / "README.md").write_text(
        "# Mini\n\n<!-- claims:start -->\n_No claims verified yet._\n<!-- claims:end -->\n",
        encoding="utf-8",
    )
    (root / "pytest.ini").write_text(
        "[pytest]\nmarkers =\n    claim(id): claim test\n", encoding="utf-8"
    )
    return root


def test_runner_end_to_end_on_a_mini_repository(tmp_path: Path) -> None:
    if shutil.which(sys.executable) is None:
        pytest.skip("no interpreter path")
    root = _mini_repo(tmp_path)
    lines: list[str] = []
    report = runner.run(root, mode="quick", echo=lines.append)
    statuses = {cid: entry["status"] for cid, entry in report["claims"].items()}
    assert statuses == {
        "C-001": "passing",
        "C-002": "failing",
        "C-003": "pending",
        "C-004": "pending",
    }
    assert report["claims"]["C-002"]["tests"] == {"tests/claims/test_mini.py::test_bad": "failed"}
    assert report["claims"]["C-004"]["tests"] == {}
    # skip reasons are the message alone: no absolute paths in a committed report
    assert report["claims"]["C-003"]["notes"] == {
        "tests/claims/test_mini.py::test_skip": "not applicable here"
    }
    assert str(tmp_path) not in json.dumps(report)
    assert report["source_digest"] == source_digest(root)
    saved = json.loads((root / runner.REPORT_JSON).read_text(encoding="utf-8"))
    assert saved["summary"] == {"pending": 2, "passing": 1, "failing": 1}
    registry_text = (root / "claims" / "claims.yaml").read_text(encoding="utf-8")
    assert "# comment that must survive" in registry_text
    assert "# trailing comment" in registry_text
    after = {c["id"]: c["status"] for c in claims_module.load(root)["claims"]}
    # pending results never overwrite an earlier definitive status (C-003 stays passing)
    assert after == {"C-001": "passing", "C-002": "failing", "C-003": "passing", "C-004": "pending"}
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "<!-- claim:C-001 -->A.<!-- /claim -->" in readme
    assert "C-002" not in readme
    assert not render_readme.update_readme(root, check=True)


def test_phase_selection_treats_missing_tests_as_errors(tmp_path: Path) -> None:
    root = _mini_repo(tmp_path)
    report = runner.run(root, phase=2, claims=["C-004"], echo=lambda _line: None, write=False)
    assert report["claims"]["C-004"]["status"] == "failing"
    assert report["claims"]["C-004"]["tests"] == {
        "tests/claims/test_missing.py::test_nothing": "error"
    }


def test_existing_nodes_finds_functions_and_methods(tmp_path: Path) -> None:
    test_file = tmp_path / "tests" / "test_x.py"
    test_file.parent.mkdir()
    test_file.write_text(
        textwrap.dedent(
            """
            def test_a(): pass
            class TestB:
                def test_c(self): pass
            """
        ),
        encoding="utf-8",
    )
    nodes = ["tests/test_x.py::test_a", "tests/test_x.py::TestB::test_c", "tests/test_x.py::nope"]
    nodes.append("tests/test_missing.py::test_a")
    assert claims_module.existing_nodes(tmp_path, nodes) == set(nodes[:2])
