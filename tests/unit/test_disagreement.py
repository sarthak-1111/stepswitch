"""The disagreement signal of best-of-N: cross tests, science outputs, clusters (SPEC §8.4)."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from stepswitch.verify import disagreement, science
from stepswitch.verify.disagreement import (
    PROBE_DIR,
    Report,
    Split,
    Subject,
    TestFile,
    TestRun,
    analyze,
    candidate_tests,
    clusters,
    compare_outputs,
    compare_tests,
    extract_test_file,
    majority,
    probe_file,
    resolve_by_tests,
    run_tests,
    tag_of,
)
from tests.claims import science_fixture

PYTHON = sys.executable

TEST_DOUBLE_NEGATIVE = (
    "from calc import double\n\n\ndef test_negative():\n    assert double(-2) == -4\n"
)
TEST_DOUBLE_POSITIVE = (
    "from calc import double\n\n\ndef test_positive():\n    assert double(3) == 6\n"
)
TEST_TRIPLE = "from calc import triple\n\n\ndef test_triple():\n    assert triple(2) == 6\n"
PROBE_WRONG = "from calc import double\n\n\ndef test_wrong():\n    assert double(1) == 3\n"


def project(path: Path, code: str) -> Subject:
    path.mkdir(parents=True)
    (path / "calc.py").write_text(code, encoding="utf-8")
    return Subject(int(path.name[1:]), path)


def two_candidates(tmp_path: Path) -> list[Subject]:
    """c0 doubles every number; c1 clamps negatives to zero and also has triple()."""
    return [
        project(tmp_path / "c0", "def double(x):\n    return 2 * x\n"),
        project(
            tmp_path / "c1",
            "def double(x):\n    return x + x if x >= 0 else 0\n\n\n"
            "def triple(x):\n    return 3 * x\n",
        ),
    ]


def test_disagreement_extracts_a_pytest_file_from_an_answer() -> None:
    fenced = "Here you go:\n```python\ndef test_one():\n    assert 1 == 1\n```\nDone."
    assert extract_test_file(fenced) == "def test_one():\n    assert 1 == 1\n"
    assert extract_test_file("def test_bare():\n    pass\n") == "def test_bare():\n    pass\n"
    blocks = "```python\ndef helper(:\n```\n```py\ndef test_second():\n    pass\n```"
    assert extract_test_file(blocks) == "def test_second():\n    pass\n"
    assert extract_test_file("```python\ndef helper():\n    pass\n```") is None
    assert extract_test_file("APPROVE") is None
    assert extract_test_file("") is None


def test_disagreement_test_files_live_under_their_tag() -> None:
    probe = probe_file("def test_x():\n    pass\n", 2)
    assert probe.tag == "probe" and probe.author is None
    assert probe.scratch_rel == f"{PROBE_DIR}/probe/test_probe_2.py" and probe.is_test
    own = TestFile("tests/test_calc.py", "", 3)
    assert own.tag == "c3" and own.scratch_rel == f"{PROBE_DIR}/c3/tests/test_calc.py"
    assert not TestFile("tests/conftest.py", "", 0).is_test
    assert tag_of(".ssw-probe.c0.tests.test_calc::test_x") == "c0"
    assert tag_of(".ssw-probe.probe.test_probe_1::test_y") == "probe"
    assert tag_of("tests.test_calc::test_x") is None


def test_disagreement_collects_the_tests_a_candidate_changed(tmp_path: Path) -> None:
    tree = tmp_path / "c1"
    (tree / "tests").mkdir(parents=True)
    (tree / "calc.py").write_text("x = 1\n", encoding="utf-8")
    (tree / "tests" / "test_calc.py").write_text(TEST_DOUBLE_POSITIVE, encoding="utf-8")
    (tree / "tests" / "conftest.py").write_text("", encoding="utf-8")
    changed = ["calc.py", "tests/test_calc.py", "tests/conftest.py", "tests/test_gone.py"]
    found = candidate_tests(1, tree, changed)
    assert [f.rel for f in found] == ["tests/conftest.py", "tests/test_calc.py"]
    assert all(f.author == 1 for f in found)
    assert found[1].text == TEST_DOUBLE_POSITIVE


def test_disagreement_reads_junit_reports_with_collection_errors(tmp_path: Path) -> None:
    report = tmp_path / "report.xml"
    report.write_text(
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">'
        '<testcase classname="" name=".ssw-probe.c1.test_triple">'
        '<error message="collection failure">ImportError</error></testcase>'
        '<testcase classname=".ssw-probe.c0.test_a" name="test_ok"/>'
        '<testcase classname=".ssw-probe.c0.test_a" name="test_bad">'
        '<failure message="assert">x</failure></testcase>'
        '<testcase classname=".ssw-probe.c0.test_a" name="test_skip">'
        '<skipped message="skip"/></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    run = disagreement._parse_junit(report)
    assert run.ran and run.broken == {".ssw-probe.c1.test_triple"}
    assert run.outcomes == {
        ".ssw-probe.c0.test_a::test_ok": "passed",
        ".ssw-probe.c0.test_a::test_bad": "failed",
        ".ssw-probe.c0.test_a::test_skip": "skipped",
    }
    assert run.status(".ssw-probe.c1.test_triple::test_triple") == "error"
    assert run.status(".ssw-probe.c0.test_other::test_x") == "missing"
    report.write_text("not xml", encoding="utf-8")
    assert not disagreement._parse_junit(report).ran


def test_disagreement_runs_tests_in_a_scratch_dir_and_cleans_up(tmp_path: Path) -> None:
    subject = two_candidates(tmp_path)[0]
    files = [TestFile("tests/test_neg.py", TEST_DOUBLE_NEGATIVE, 0), probe_file(PROBE_WRONG)]
    run = run_tests(subject, files, python=PYTHON)
    assert run.ran, run.detail
    assert run.outcomes == {
        ".ssw-probe.c0.tests.test_neg::test_negative": "passed",
        ".ssw-probe.probe.test_probe_1::test_wrong": "failed",
    }
    assert not (subject.path / PROBE_DIR).exists()
    assert not list(subject.path.rglob("__pycache__"))
    assert not run_tests(subject, [TestFile("tests/conftest.py", "", 0)], python=PYTHON).ran


def test_disagreement_cross_tests_split_candidates(tmp_path: Path) -> None:
    subjects = two_candidates(tmp_path)
    files = [
        TestFile("tests/test_neg.py", TEST_DOUBLE_NEGATIVE, 0),
        TestFile("tests/test_pos.py", TEST_DOUBLE_POSITIVE, 1),
        TestFile("tests/test_triple.py", TEST_TRIPLE, 1),
        probe_file(PROBE_WRONG),
    ]
    splits, vectors, notes = compare_tests(subjects, files, python=PYTHON)
    assert notes == []
    names = {split.name: split.outcomes for split in splits}
    # c0's own test fails on c1; c1's triple() test cannot even be collected on c0
    assert names[".ssw-probe.c0.tests.test_neg::test_negative"] == {0: "passed", 1: "failed"}
    assert names[".ssw-probe.c1.tests.test_triple::test_triple"] == {0: "error", 1: "passed"}
    assert len(splits) == 2
    # the shared behaviour agrees, and a probe test that passes nowhere does not count
    assert vectors[0][".ssw-probe.c1.tests.test_pos::test_positive"] is True
    assert vectors[1][".ssw-probe.c1.tests.test_pos::test_positive"] is True
    assert not any("test_wrong" in key for key in vectors[0])


def test_disagreement_analyze_flags_and_counts(tmp_path: Path) -> None:
    subjects = two_candidates(tmp_path)
    files = [
        TestFile("tests/test_neg.py", TEST_DOUBLE_NEGATIVE, 0),
        TestFile("tests/test_pos.py", TEST_DOUBLE_POSITIVE, 1),
        probe_file(TEST_DOUBLE_POSITIVE.replace("test_positive", "test_probe")),
    ]
    report = analyze(subjects, files, python=PYTHON, policy="tests")
    assert report.detected and report.uncertain and report.policy == "tests"
    assert report.clusters == [[0], [1]]
    assert report.tests == 3 and report.probe_tests == 1
    assert report.passed == {0: 3, 1: 2}
    data = report.as_dict()
    assert data["uncertain"] is True and data["passed"] == {"0": 3, "1": 2}
    assert data["splits"][0]["outcomes"] == {"0": "passed", "1": "failed"}
    # the same candidates agree when only shared behaviour is tested
    agreed = analyze(subjects, files[1:], python=PYTHON)
    assert not agreed.detected and agreed.clusters == [[0, 1]]


def test_disagreement_needs_two_candidates_and_some_tests(tmp_path: Path) -> None:
    subjects = two_candidates(tmp_path)
    alone = analyze(subjects[:1], [], python=PYTHON)
    assert not alone.detected and alone.clusters == [[0]]
    assert "fewer than two passing candidates" in alone.notes[0]
    untested = analyze(subjects, [], python=PYTHON)
    assert not untested.detected and untested.clusters == [[0, 1]]
    assert "no candidate or probe tests to cross-run" in untested.notes


def test_disagreement_compares_science_outputs_pairwise(tmp_path: Path) -> None:
    repo = science_fixture.make_project(tmp_path / "base")
    science_fixture.write_science_toml(
        repo,
        [
            {
                "name": "spectrum",
                "file": "spectrum.json",
                "key": "y",
                "x_key": "x",
                "mode": "elementwise",
                "rtol": 0.05,
                "atol": 1e-9,
            }
        ],
    )
    config = science.load_science(repo)
    subjects = []
    for index, scale in enumerate(("-0.04", "0.04", "0.002")):
        tree = tmp_path / f"c{index}"
        shutil.copytree(repo, tree, ignore=shutil.ignore_patterns(".git"))
        science_fixture.set_constant(tree / "toy" / "spectra.py", "SCALE", scale)
        subjects.append(Subject(index, tree))
    splits, differ, compared, notes = compare_outputs(subjects, config, python=PYTHON)
    assert notes == [] and compared == 1
    # -4% and +4% are 8% apart; each is within 5% of the 0.2% candidate
    assert differ == {(0, 1)}
    assert [(s.kind, s.name) for s in splits] == [("output", "peaks/spectrum")]
    assert splits[0].outcomes == {0: "reference", 1: "differs"} and splits[0].detail
    report = analyze(subjects[:2], [], python=PYTHON, science_config=config)
    assert report.detected and report.outputs == 1 and report.clusters == [[0], [1]]


def test_disagreement_clusters_majority_and_targeted_resolution() -> None:
    vectors = {0: {"t1": True, "t2": False}, 1: {"t1": True}, 2: {"t1": False, "t2": True}}
    assert clusters([0, 1, 2], vectors, set()) == [[0, 1], [2]]
    assert clusters([0, 1, 2], vectors, {(0, 1)}) == [[0], [1], [2]]
    assert majority([[0, 1], [2]]) == [0, 1]
    assert majority([[0], [1]]) is None
    assert majority([]) is None
    report = Report(detected=True, candidates=[0, 1, 2], clusters=[[0, 1], [2]])
    subjects = [Subject(i, Path(f"c{i}")) for i in range(3)]
    key = ".ssw-probe.probe.test_targeted::test_case"
    decisive = {
        0: TestRun({key: "passed"}),
        1: TestRun({key: "passed"}),
        2: TestRun({key: "failed"}),
    }
    assert resolve_by_tests(report, subjects, decisive) == [0, 1]
    split_cluster = {0: TestRun({key: "passed"}), 1: TestRun({key: "failed"}), 2: decisive[2]}
    assert resolve_by_tests(report, subjects, split_cluster) is None
    nowhere = {i: TestRun({key: "failed"}) for i in range(3)}
    assert resolve_by_tests(report, subjects, nowhere) is None
    missing = {0: TestRun({key: "passed"}), 1: TestRun({key: "passed"})}
    assert resolve_by_tests(report, subjects, missing) == [0, 1]


def test_disagreement_report_uncertainty() -> None:
    assert not Report().uncertain
    flagged = Report(detected=True, splits=[Split("test", "k", {0: "passed", 1: "failed"})])
    assert flagged.uncertain
    flagged.resolved = True
    assert not flagged.uncertain and flagged.as_dict()["uncertain"] is False
    assert disagreement.POLICIES == ("flag", "tests", "escalate", "human")
