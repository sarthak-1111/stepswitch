"""The disagreement signal of best-of-N (SPEC §8.4, ADR 0025).

Candidates that all pass the gates can still behave differently: the instruction allowed two
readings, or one candidate is wrong in a way the project's tests do not catch. `analyze`
looks for behaviour that tells passing candidates apart.

* **Cross tests.** Every test file a candidate added or changed runs against the code of every
  passing candidate. So do probe tests: tests written for the task by a model that has not
  seen any candidate's code (the orchestrator asks for them). The files are copied into a
  scratch directory, `PROBE_DIR`, inside each candidate's worktree and run with pytest (import
  mode `importlib`, a JUnit XML report, no cache, no bytecode, the project's `addopts`
  cleared), then removed. A test counts only when it passes somewhere: a candidate's test on
  its author's code, a probe test on at least one candidate. A test that passes on one
  candidate and fails or errors on another splits them.
* **Science outputs.** In the science track each golden case runs once per passing candidate,
  and the candidates' outputs are compared with each other, quantity by quantity, with the
  case's own mode and tolerances (`science.compare`, the lower-numbered candidate's output as
  the reference). A quantity beyond tolerance splits the pair.

Two candidates agree when no test splits them and their outputs match. Candidates that agree
form a cluster; the task is flagged (`Report.detected`) when there is more than one cluster.
`POLICIES` names what the orchestrator does then:

* `flag` (default): mark the task uncertain, keep the selected candidate, apply nothing;
* `tests`: ask for targeted tests on the split behaviour and keep the cluster that passes
  them (`resolve_by_tests`); still uncertain when they do not decide;
* `escalate`: run one more candidate and keep the majority cluster (`majority`);
* `human`: end the task as `needs-human`, keeping every candidate's branch.

Nothing here talks to a model or the network.
"""

from __future__ import annotations

import ast
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from stepswitch.verify import commands, impact, science

POLICIES = ("flag", "tests", "escalate", "human")
DEFAULT_POLICY = "flag"
PROBE_DIR = ".ssw-probe"
PROBE_TAG = "probe"
PYTEST_ARGS = (
    "-q",
    "-p",
    "no:cacheprovider",
    "--import-mode=importlib",
    "-o",
    "addopts=",
    "--continue-on-collection-errors",
)
TEST_TIMEOUT = 600.0
MAX_TEST_FILES = 20
MAX_TEST_BYTES = 200_000
MAX_SPLITS = 50
_FENCE = re.compile(r"```(?:python|py)?[ \t]*\n(.*?)```", re.S | re.I)
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


@dataclass(frozen=True)
class TestFile:
    """A test file to cross-run: its path in its author's worktree, its text, its author."""

    __test__ = False  # not a pytest test class

    rel: str
    text: str
    # The candidate that wrote it; None for a probe test.
    author: int | None = None

    @property
    def tag(self) -> str:
        return PROBE_TAG if self.author is None else f"c{self.author}"

    @property
    def scratch_rel(self) -> str:
        return f"{PROBE_DIR}/{self.tag}/{self.rel}"

    @property
    def is_test(self) -> bool:
        return impact.is_test_file(self.rel)


@dataclass(frozen=True)
class Subject:
    """A passing candidate as the comparison sees it: its index and worktree."""

    index: int
    path: Path


@dataclass
class Split:
    kind: str  # "test" or "output"
    name: str
    outcomes: dict[int, str]
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "name": self.name,
            "outcomes": {str(k): v for k, v in sorted(self.outcomes.items())},
            "detail": self.detail,
        }


@dataclass
class Report:
    detected: bool = False
    candidates: list[int] = field(default_factory=list)
    clusters: list[list[int]] = field(default_factory=list)
    splits: list[Split] = field(default_factory=list)
    # Valid tests compared (of them probe tests), quantities compared.
    tests: int = 0
    probe_tests: int = 0
    outputs: int = 0
    # Valid tests each candidate passed.
    passed: dict[int, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    policy: str = DEFAULT_POLICY
    resolved: bool = False
    resolution: str = ""
    # The candidates a policy kept when it resolved the disagreement.
    kept: list[int] = field(default_factory=list)

    @property
    def uncertain(self) -> bool:
        return self.detected and not self.resolved

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["splits"] = [split.as_dict() for split in self.splits[:MAX_SPLITS]]
        data["passed"] = {str(k): v for k, v in sorted(self.passed.items())}
        data["uncertain"] = self.uncertain
        return data


# --------------------------------------------------------------------------------------------
# Test files
# --------------------------------------------------------------------------------------------


def candidate_tests(index: int, worktree: Path, changed: Sequence[str]) -> list[TestFile]:
    """The test files (and conftest files) a candidate added or changed."""
    found: list[TestFile] = []
    for rel in sorted(changed):
        name = PurePosixPath(rel).name
        if not (impact.is_test_file(rel) or name == "conftest.py"):
            continue
        path = worktree / rel
        if not path.is_file() or path.stat().st_size > MAX_TEST_BYTES:
            continue
        found.append(TestFile(rel, path.read_text(encoding="utf-8", errors="replace"), index))
    return found[:MAX_TEST_FILES]


def extract_test_file(text: str) -> str | None:
    """The pytest file in a model's answer (a fenced block or bare code), or None.

    The code must parse and define at least one test function.
    """
    blocks = [match.group(1) for match in _FENCE.finditer(text)] or [text]
    for block in blocks:
        code = block.strip("\n")
        try:
            tree = ast.parse(code)
        except SyntaxError:
            continue
        if any(
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name.startswith("test")
            for node in ast.walk(tree)
        ):
            return code + "\n"
    return None


def probe_file(text: str, number: int = 1) -> TestFile:
    return TestFile(f"test_probe_{number}.py", text, None)


# --------------------------------------------------------------------------------------------
# Running tests in a candidate's worktree
# --------------------------------------------------------------------------------------------


@dataclass
class TestRun:
    """Outcomes of one pytest run: test id -> passed, failed, error or skipped."""

    __test__ = False  # not a pytest test class

    outcomes: dict[str, str] = field(default_factory=dict)
    # Dotted names of modules that could not be collected.
    broken: set[str] = field(default_factory=set)
    ran: bool = True
    detail: str = ""

    def status(self, key: str) -> str:
        if key in self.outcomes:
            return self.outcomes[key]
        module = key.split("::", 1)[0]
        if any(module == m or module.startswith(m + ".") for m in self.broken):
            return "error"
        return "missing"


def _parse_junit(path: Path) -> TestRun:
    run = TestRun()
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        return TestRun(ran=False, detail=f"no JUnit report: {exc}")
    for case in root.iter("testcase"):
        classname, name = case.get("classname") or "", case.get("name") or ""
        tags = {child.tag for child in case}
        if not classname and "error" in tags:
            run.broken.add(name)
            continue
        if "failure" in tags:
            status = "failed"
        elif "error" in tags:
            status = "error"
        elif "skipped" in tags:
            status = "skipped"
        else:
            status = "passed"
        run.outcomes[f"{classname}::{name}"] = status
    return run


def run_tests(
    subject: Subject,
    files: Sequence[TestFile],
    *,
    python: str,
    env: Mapping[str, str] | None = None,
    timeout: float = TEST_TIMEOUT,
) -> TestRun:
    """Run `files` against the candidate's code; the scratch directory is removed afterwards."""
    targets = [f.scratch_rel for f in files if f.is_test]
    if not targets:
        return TestRun(ran=False, detail="no test files")
    scratch = subject.path / PROBE_DIR
    shutil.rmtree(scratch, ignore_errors=True)
    try:
        for item in files:
            path = subject.path / item.scratch_rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(item.text, encoding="utf-8")
        with tempfile.TemporaryDirectory(prefix="ssw-probe-") as tmp:
            report = Path(tmp) / "report.xml"
            argv = [
                python,
                "-B",
                "-m",
                "pytest",
                *PYTEST_ARGS,
                f"--junitxml={report}",
                f"--rootdir={subject.path}",
                *targets,
            ]
            result = commands.run(argv, cwd=subject.path, timeout=timeout, env=env)
            if not result.started:
                return TestRun(ran=False, detail=result.start_error)
            if result.timed_out:
                return TestRun(ran=False, detail=f"the tests timed out after {timeout:g} s")
            run = _parse_junit(report)
            if not run.ran:
                run.detail += f" (pytest exit code {result.returncode})"
            return run
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def tag_of(key: str) -> str | None:
    """The scratch tag (`c<i>` or `probe`) a test id lives under."""
    parts = key.split("::", 1)[0].split(".")
    # ".ssw-probe.c0.tests.test_x" splits into ["", "ssw-probe", "c0", ...].
    if len(parts) > 2 and parts[1] == PROBE_DIR.lstrip("."):
        return parts[2]
    return None


def compare_tests(
    subjects: Sequence[Subject],
    files: Sequence[TestFile],
    *,
    python: str,
    env: Mapping[str, str] | None = None,
    timeout: float = TEST_TIMEOUT,
) -> tuple[list[Split], dict[int, dict[str, bool]], list[str]]:
    """(splits, valid-test outcomes per candidate, notes) for the cross tests."""
    notes: list[str] = []
    runs = {s.index: run_tests(s, files, python=python, env=env, timeout=timeout) for s in subjects}
    for index, run in runs.items():
        if not run.ran:
            notes.append(f"candidate {index}: tests did not run ({run.detail})")
    ran = {index: run for index, run in runs.items() if run.ran}
    keys = sorted({key for run in ran.values() for key in run.outcomes})
    splits: list[Split] = []
    vectors: dict[int, dict[str, bool]] = {index: {} for index in ran}
    for key in keys:
        outcomes = {index: run.status(key) for index, run in ran.items()}
        tag = tag_of(key)
        author = int(tag[1:]) if tag and tag.startswith("c") and tag[1:].isdigit() else None
        if author is not None and author in outcomes:
            valid = outcomes[author] == "passed"
        else:
            valid = any(status == "passed" for status in outcomes.values())
        if not valid:
            continue
        for index, status in outcomes.items():
            vectors[index][key] = status == "passed"
        if len({status == "passed" for status in outcomes.values()}) > 1:
            splits.append(Split("test", key, outcomes))
    return splits, vectors, notes


# --------------------------------------------------------------------------------------------
# Science outputs
# --------------------------------------------------------------------------------------------


def compare_outputs(
    subjects: Sequence[Subject],
    config: science.ScienceConfig,
    *,
    python: str,
    env: Mapping[str, str] | None = None,
    timeout: float = TEST_TIMEOUT,
) -> tuple[list[Split], set[tuple[int, int]], int, list[str]]:
    """(splits, pairs that differ, quantities compared, notes) for the golden outputs.

    Each golden case runs once per candidate; every pair of candidates is then compared
    quantity by quantity with the case's mode and tolerances.
    """
    notes: list[str] = []
    splits: list[Split] = []
    differ: set[tuple[int, int]] = set()
    compared = 0
    base_env = dict(env) if env is not None else commands.project_env(python)
    with tempfile.TemporaryDirectory(prefix="ssw-outputs-") as tmp:
        for case in config.golden:
            outputs: dict[int, tuple[Path, dict[str, str]]] = {}
            for subject in subjects:
                out = Path(tmp) / f"c{subject.index}" / _UNSAFE.sub("_", case.case)
                out.mkdir(parents=True, exist_ok=True)
                commands.purge_stale_bytecode(subject.path)
                result = science.run_case_command(
                    subject.path,
                    case,
                    python=python,
                    env=science.golden_env(subject.path, base_env),
                    timeout=timeout,
                    output=out,
                )
                if not result.ok:
                    notes.append(f"case {case.case} failed on candidate {subject.index}")
                    continue
                values = science.case_values(subject.path, case, python=python, output=out)
                outputs[subject.index] = (out, values)
            indices = sorted(outputs)
            for quantity in case.quantities:
                arrays: dict[int, tuple[Any, Any]] = {}
                for index in indices:
                    out, values = outputs[index]
                    path = science.output_path(quantity, out, values)
                    try:
                        data = science.read_array(path, quantity.data_key)
                        x = (
                            science.read_array(path, quantity.x_key, keyed=True)
                            if quantity.x_key
                            else None
                        )
                    except science.DataError as exc:
                        notes.append(f"{case.case}/{quantity.name} on candidate {index}: {exc}")
                        continue
                    arrays[index] = (data, x)
                ordered = sorted(arrays)
                if len(ordered) >= 2:
                    compared += 1
                for position, first in enumerate(ordered):
                    for second in ordered[position + 1 :]:
                        (e, xe), (a, xa) = arrays[first], arrays[second]
                        try:
                            measured = science.compare(
                                quantity.mode,
                                e,
                                a,
                                rtol=quantity.rtol,
                                atol=quantity.atol,
                                min_overlap=quantity.min_overlap,
                                min_peak_height=(
                                    quantity.min_peak_height
                                    if quantity.min_peak_height is not None
                                    else config.science.min_peak_height
                                ),
                                x_expected=xe,
                                x_actual=xa,
                            )
                        except science.DataError as exc:
                            notes.append(f"{case.case}/{quantity.name}: {exc}")
                            continue
                        if measured.passed:
                            continue
                        differ.add((first, second))
                        splits.append(
                            Split(
                                "output",
                                f"{case.case}/{quantity.name}",
                                {first: "reference", second: "differs"},
                                science.measured_text(measured),
                            )
                        )
    return splits, differ, compared, notes


# --------------------------------------------------------------------------------------------
# Analysis and resolution
# --------------------------------------------------------------------------------------------


def clusters(
    indices: Sequence[int],
    vectors: Mapping[int, Mapping[str, bool]],
    differ: set[tuple[int, int]],
) -> list[list[int]]:
    """Groups of candidates that agree: the same outcome on every valid test (a test one of
    them did not run counts as failed) and outputs within tolerance of each other."""
    keys = sorted({key for vector in vectors.values() for key in vector})

    def agree(a: int, b: int) -> bool:
        if (min(a, b), max(a, b)) in differ:
            return False
        va, vb = vectors.get(a, {}), vectors.get(b, {})
        return all(va.get(key, False) == vb.get(key, False) for key in keys)

    groups: list[list[int]] = []
    for index in sorted(indices):
        for group in groups:
            if all(agree(index, other) for other in group):
                group.append(index)
                break
        else:
            groups.append([index])
    return groups


def analyze(
    subjects: Sequence[Subject],
    files: Sequence[TestFile],
    *,
    python: str,
    env: Mapping[str, str] | None = None,
    science_config: science.ScienceConfig | None = None,
    timeout: float = TEST_TIMEOUT,
    policy: str = DEFAULT_POLICY,
) -> Report:
    """Compare passing candidates (see the module docstring)."""
    report = Report(candidates=[s.index for s in subjects], policy=policy)
    if len(subjects) < 2:
        report.clusters = [[s.index] for s in subjects]
        report.notes.append("fewer than two passing candidates: nothing to compare")
        return report
    test_splits: list[Split] = []
    vectors: dict[int, dict[str, bool]] = {}
    if any(f.is_test for f in files):
        test_splits, vectors, notes = compare_tests(
            subjects, files, python=python, env=env, timeout=timeout
        )
        report.notes += notes
    else:
        report.notes.append("no candidate or probe tests to cross-run")
    keys = {key for vector in vectors.values() for key in vector}
    report.tests = len(keys)
    report.probe_tests = sum(1 for key in keys if tag_of(key) == PROBE_TAG)
    report.passed = {index: sum(vector.values()) for index, vector in vectors.items()}
    differ: set[tuple[int, int]] = set()
    output_splits: list[Split] = []
    if science_config is not None and science_config.golden:
        output_splits, differ, report.outputs, notes = compare_outputs(
            subjects, science_config, python=python, env=env, timeout=timeout
        )
        report.notes += notes
    report.splits = test_splits + output_splits
    report.clusters = clusters(report.candidates, vectors, differ)
    report.detected = len(report.clusters) > 1
    return report


def resolve_by_tests(
    report: Report, subjects: Sequence[Subject], run: Mapping[int, TestRun]
) -> list[int] | None:
    """The candidates of the one cluster that passes every targeted test that passes anywhere,
    or None when the targeted tests do not single out one cluster."""
    keys = sorted({key for r in run.values() if r.ran for key in r.outcomes})
    valid = [k for k in keys if any(r.status(k) == "passed" for r in run.values() if r.ran)]
    if not valid:
        return None
    winners = {
        s.index
        for s in subjects
        if (r := run.get(s.index)) is not None
        and r.ran
        and all(r.status(k) == "passed" for k in valid)
    }
    for group in report.clusters:
        if winners and winners == set(group):
            return sorted(group)
    return None


def majority(groups: Sequence[Sequence[int]]) -> list[int] | None:
    """The largest cluster when it is strictly larger than every other one."""
    ordered = sorted(groups, key=len, reverse=True)
    if not ordered or (len(ordered) > 1 and len(ordered[0]) == len(ordered[1])):
        return None
    return sorted(ordered[0])
