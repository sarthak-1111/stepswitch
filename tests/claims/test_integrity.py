"""Integrity meta-tests for the claims registry (SPEC §18.3).

They run from day one and keep public text honest:

1. Every claim has at least one test, and every claim test maps to a registered claim.
2. Text inside ``<!-- claim:C-0xx -->`` markers in public docs matches claims.yaml exactly, and
   unmarked superlatives or comparatives fail the lint.
3. Every number in the README is rendered from an evidence file, never typed by hand.
4. The README claims section is generated and lists only passing claims.

The lint helpers are also exercised against synthetic text at the end of this file, so the
checks stay meaningful while the public docs are still nearly empty.
"""

from __future__ import annotations

import ast
import importlib
import json
import re
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[2]
REGISTRY_PATH = ROOT / "claims" / "claims.yaml"
EVIDENCE_DIR = ROOT / "claims" / "evidence"
CLAIM_TESTS_DIR = ROOT / "tests" / "claims"
README_PATH = ROOT / "README.md"
SPEC_PATH = ROOT / "docs" / "SPEC.md"

CLAIM_ID_RE = re.compile(r"^C-\d{3}$")
NODE_ID_RE = re.compile(r"^tests/claims/test_[a-z0-9_]+\.py::[A-Za-z_]\w*(::[A-Za-z_]\w*)?$")
KINDS = {"unit", "integration", "e2e", "bench", "probe", "platform"}
STATUSES = {"pending", "passing", "failing"}
REQUIRED_FIELDS = (
    "id",
    "text",
    "scope",
    "proof",
    "kind",
    "phase",
    "thresholds",
    "evidence",
    "status",
)
OPTIONAL_FIELDS = ("tests", "parts", "narrowed_by", "readme_numbers")
SPEC_CLAIM_IDS = [f"C-{n:03d}" for n in range(1, 21)]

CLAIM_SPAN_RE = re.compile(r"<!--\s*claim:(C-\d{3})\s*-->(.*?)<!--\s*/claim\s*-->", re.S)
NUM_SPAN_RE = re.compile(r"<!--\s*num:(C-\d{3}):([A-Za-z0-9_.]+)\s*-->(.*?)<!--\s*/num\s*-->", re.S)
CLAIMS_SECTION_RE = re.compile(r"<!--\s*claims:start\s*-->(.*?)<!--\s*claims:end\s*-->", re.S)
FENCE_RE = re.compile(r"^(```|~~~).*?^\1[ \t]*$", re.S | re.M)
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
LINK_TARGET_RE = re.compile(r"\]\([^)]*\)")
URL_RE = re.compile(r"https?://\S+")
NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")

Registry = dict[str, Any]


# --------------------------------------------------------------------------------------------
# Registry helpers
# --------------------------------------------------------------------------------------------


def load_registry(path: Path = REGISTRY_PATH) -> Registry:
    data = YAML(typ="safe", pure=True).load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict), "claims.yaml must be a mapping"
    return data


def claims_by_id(registry: Registry) -> dict[str, dict[str, Any]]:
    return {claim["id"]: claim for claim in registry["claims"]}


def claim_tests(claim: dict[str, Any]) -> list[str]:
    if "parts" in claim:
        return [test for part in claim["parts"] for test in part.get("tests", [])]
    return list(claim.get("tests", []))


def spec_claim_texts(spec_path: Path = SPEC_PATH) -> dict[str, str]:
    """Claim wording from the SPEC §18.5 table."""
    texts: dict[str, str] = {}
    for line in spec_path.read_text(encoding="utf-8").splitlines():
        cells = [cell.strip() for cell in line.split("|")]
        if len(cells) > 3 and CLAIM_ID_RE.match(cells[1]):
            texts[cells[1]] = cells[2]
    return texts


# The phases of scripts/phase_check.py: SPEC §23's ten, then the frontier comparison (ADR 0034).
MAX_PHASE = 11


# --------------------------------------------------------------------------------------------
# Static scan of claim markers in tests/claims (no imports, no collection side effects)
# --------------------------------------------------------------------------------------------


def _is_claim_mark(func: ast.expr) -> bool:
    if not (isinstance(func, ast.Attribute) and func.attr == "claim"):
        return False
    owner = func.value
    return (isinstance(owner, ast.Attribute) and owner.attr == "mark") or (
        isinstance(owner, ast.Name) and owner.id == "mark"
    )


def _claim_ids_in(expr: ast.AST) -> set[str]:
    ids: set[str] = set()
    for sub in ast.walk(expr):
        if isinstance(sub, ast.Call) and _is_claim_mark(sub.func):
            ids.update(
                arg.value
                for arg in sub.args
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            )
    return ids


def _pytestmark_ids(body: list[ast.stmt]) -> set[str]:
    ids: set[str] = set()
    for stmt in body:
        if isinstance(stmt, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "pytestmark" for target in stmt.targets
        ):
            ids |= _claim_ids_in(stmt.value)
    return ids


def _decorator_ids(decorators: list[ast.expr]) -> set[str]:
    ids: set[str] = set()
    for decorator in decorators:
        ids |= _claim_ids_in(decorator)
    return ids


def _iter_tests(tree: ast.Module) -> Iterator[tuple[str, set[str]]]:
    module_ids = _pytestmark_ids(tree.body)
    for stmt in tree.body:
        if isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef) and stmt.name.startswith(
            "test"
        ):
            yield stmt.name, module_ids | _decorator_ids(stmt.decorator_list)
        elif isinstance(stmt, ast.ClassDef) and stmt.name.startswith("Test"):
            class_ids = (
                module_ids | _decorator_ids(stmt.decorator_list) | _pytestmark_ids(stmt.body)
            )
            for item in stmt.body:
                if isinstance(item, ast.FunctionDef | ast.AsyncFunctionDef) and (
                    item.name.startswith("test")
                ):
                    yield (
                        f"{stmt.name}::{item.name}",
                        class_ids | _decorator_ids(item.decorator_list),
                    )


def marked_claim_tests(tests_dir: Path = CLAIM_TESTS_DIR, root: Path = ROOT) -> dict[str, set[str]]:
    """Map every test node ID under ``tests_dir`` to the claim IDs it is marked with."""
    found: dict[str, set[str]] = {}
    for path in sorted(tests_dir.glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        rel = path.relative_to(root).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for name, ids in _iter_tests(tree):
            found[f"{rel}::{name}"] = ids
    return found


# --------------------------------------------------------------------------------------------
# Public-text lint
# --------------------------------------------------------------------------------------------


def _norm(text: str) -> str:
    return " ".join(text.split())


def strip_non_prose(text: str) -> str:
    """Drop markers, comments, code, link targets and URLs, leaving the prose a reader sees."""
    text = CLAIM_SPAN_RE.sub(" ", text)
    text = NUM_SPAN_RE.sub(" ", text)
    text = HTML_COMMENT_RE.sub(" ", text)
    text = FENCE_RE.sub(" ", text)
    text = INLINE_CODE_RE.sub(" ", text)
    text = LINK_TARGET_RE.sub("]", text)
    return URL_RE.sub(" ", text)


def lint_public_text(text: str, registry: Registry) -> list[str]:
    """Marker wording must match the registry; unmarked superlatives fail."""
    claims = claims_by_id(registry)
    problems: list[str] = []
    for claim_id, inner in CLAIM_SPAN_RE.findall(text):
        claim = claims.get(claim_id)
        if claim is None:
            problems.append(f"marker references unregistered claim {claim_id}")
        elif _norm(inner) != _norm(claim["text"]):
            problems.append(f"{claim_id}: marked text differs from claims.yaml: {inner.strip()!r}")
    prose = strip_non_prose(text)
    for pattern in registry["lint"]["banned_unmarked"]:
        for match in re.finditer(pattern, prose, re.IGNORECASE):
            problems.append(f"unmarked superlative or comparative {match.group(0)!r}")
    return problems


def _evidence_value(evidence_dir: Path, claim_id: str, key: str) -> Any:
    path = evidence_dir / f"{claim_id}.json"
    if not path.is_file():
        return None
    value: Any = json.loads(path.read_text(encoding="utf-8"))
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _rendered_matches(rendered: str, value: Any) -> bool:
    match = NUMBER_RE.search(rendered)
    if match is None or isinstance(value, bool) or not isinstance(value, int | float):
        return False
    shown = match.group(0).replace(",", "")
    decimals = len(shown.split(".")[1]) if "." in shown else 0
    return round(float(value), decimals) == float(shown)


def lint_number_markers(text: str, evidence_dir: Path = EVIDENCE_DIR) -> list[str]:
    """Every num marker must show its evidence value at the displayed precision."""
    problems: list[str] = []
    for claim_id, key, rendered in NUM_SPAN_RE.findall(text):
        value = _evidence_value(evidence_dir, claim_id, key)
        if value is None:
            problems.append(f"num marker {claim_id}:{key} has no value in the evidence file")
        elif not _rendered_matches(rendered, value):
            problems.append(
                f"num marker {claim_id}:{key} shows {rendered!r}, evidence has {value!r}"
            )
    return problems


def lint_hand_typed_numbers(text: str, registry: Registry) -> list[str]:
    """Outside markers, digits may only appear in claim IDs, section references and versions."""
    prose = strip_non_prose(text)
    for pattern in registry["lint"]["readme_number_allowlist"]:
        prose = re.sub(pattern, " ", prose)
    return [
        f"hand-typed number in README: {line.strip()!r}"
        for line in prose.splitlines()
        if re.search(r"\d", line)
    ]


def lint_readme_numbers(
    text: str, registry: Registry, evidence_dir: Path = EVIDENCE_DIR
) -> list[str]:
    """Numbers must be rendered from evidence (num markers) or be part of registered claim text."""
    return lint_number_markers(text, evidence_dir) + lint_hand_typed_numbers(text, registry)


def _readme_renderer() -> Callable[..., str] | None:
    """The generator behind `ssw prove --render-readme`, once it exists (Phase 1)."""
    try:
        module = importlib.import_module("stepswitch.prove.render_readme")
    except ImportError:
        return None
    renderer = getattr(module, "render_claims_section", None)
    return renderer if callable(renderer) else None


def public_doc_paths(registry: Registry, root: Path = ROOT) -> list[Path]:
    paths: set[Path] = set()
    for pattern in registry["lint"]["public_docs"]:
        paths.update(path for path in root.glob(pattern) if path.is_file())
    return sorted(paths)


# --------------------------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def registry() -> Registry:
    return load_registry()


@pytest.fixture(scope="module")
def marked() -> dict[str, set[str]]:
    return marked_claim_tests()


# --------------------------------------------------------------------------------------------
# Rule 1: every claim has tests; every claim test maps to a registered claim
# --------------------------------------------------------------------------------------------


def test_registry_schema(registry: Registry) -> None:
    assert registry.get("version") == 1
    for key in ("public_docs", "banned_unmarked", "readme_number_allowlist"):
        assert registry["lint"][key], f"lint.{key} must not be empty"
    ids = [claim["id"] for claim in registry["claims"]]
    assert len(ids) == len(set(ids)), "claim IDs must be unique"
    for claim in registry["claims"]:
        cid = claim.get("id", "<missing id>")
        assert CLAIM_ID_RE.match(cid), f"bad claim id {cid!r}"
        missing = [field for field in REQUIRED_FIELDS if field not in claim]
        assert not missing, f"{cid}: missing fields {missing}"
        unknown = set(claim) - set(REQUIRED_FIELDS) - set(OPTIONAL_FIELDS)
        assert not unknown, f"{cid}: unknown fields {sorted(unknown)}"
        assert ("tests" in claim) != ("parts" in claim), f"{cid}: use exactly one of tests/parts"
        assert claim["kind"] in KINDS, f"{cid}: kind {claim['kind']!r}"
        assert claim["status"] in STATUSES, f"{cid}: status {claim['status']!r}"
        assert isinstance(claim["phase"], int) and 0 <= claim["phase"] <= MAX_PHASE, f"{cid}: phase"
        assert claim["text"].strip() and claim["scope"].strip() and claim["proof"].strip()
        assert isinstance(claim["thresholds"], dict) and claim["thresholds"], f"{cid}: thresholds"
        assert claim["evidence"] == [f"claims/evidence/{cid}.json"], f"{cid}: evidence path"
        for part in claim.get("parts", []):
            assert {"name", "phase", "tests"} <= set(part), f"{cid}: part fields"
            assert part["phase"] >= claim["phase"], f"{cid}: part phase before claim phase"
        if "parts" in claim:
            assert min(part["phase"] for part in claim["parts"]) == claim["phase"]
        if "narrowed_by" in claim:
            assert (ROOT / claim["narrowed_by"]).is_file(), f"{cid}: narrowing ADR missing"
        for number in claim.get("readme_numbers", []):
            assert set(number) <= {"key", "label", "unit", "digits"}, f"{cid}: {number}"
            assert re.fullmatch(r"[A-Za-z0-9_.]+", number["key"]), f"{cid}: number key"
            assert number["label"].strip() and not re.search(r"\d", number["label"]), cid
            assert number.get("digits", 0) in range(5), f"{cid}: digits"


def test_registry_holds_every_spec_claim_with_spec_wording(registry: Registry) -> None:
    claims = claims_by_id(registry)
    spec = spec_claim_texts()
    assert sorted(spec) == SPEC_CLAIM_IDS, "SPEC §18.5 table could not be parsed"
    for cid in SPEC_CLAIM_IDS:
        assert cid in claims, f"{cid} from SPEC §18.5 is not registered"
        if "narrowed_by" not in claims[cid]:
            assert claims[cid]["text"] == spec[cid], f"{cid}: wording differs from SPEC §18.5"


def test_every_claim_has_at_least_one_test(registry: Registry) -> None:
    for claim in registry["claims"]:
        tests = claim_tests(claim)
        assert tests, f"{claim['id']} has no tests"
        for node_id in tests:
            assert NODE_ID_RE.match(node_id), f"{claim['id']}: bad node id {node_id!r}"
    all_tests = [test for claim in registry["claims"] for test in claim_tests(claim)]
    assert len(all_tests) == len(set(all_tests)), "a test node is registered under two claims"


def test_every_claim_test_maps_to_a_registered_claim(
    registry: Registry, marked: dict[str, set[str]]
) -> None:
    claims = claims_by_id(registry)
    for node_id, ids in marked.items():
        assert ids, f"{node_id} lives in tests/claims but has no claim marker"
        for cid in ids:
            assert cid in claims, f"{node_id} is marked with unregistered claim {cid}"
            assert node_id in claim_tests(claims[cid]), f"{node_id} is not listed under {cid}"


def test_claims_beyond_pending_have_their_marked_tests(
    registry: Registry, marked: dict[str, set[str]]
) -> None:
    for claim in registry["claims"]:
        if claim["status"] == "pending":
            continue
        for node_id in claim_tests(claim):
            assert node_id in marked, f"{claim['id']}: test {node_id} does not exist"
            assert claim["id"] in marked[node_id], f"{node_id} lacks claim({claim['id']!r})"


# --------------------------------------------------------------------------------------------
# Rule 2: public docs lint
# --------------------------------------------------------------------------------------------


def test_public_docs_pass_the_claims_lint(registry: Registry) -> None:
    problems = {
        path.relative_to(ROOT).as_posix(): lint_public_text(
            path.read_text(encoding="utf-8"), registry
        )
        for path in public_doc_paths(registry)
    }
    assert not {doc: found for doc, found in problems.items() if found}


def test_public_docs_show_only_passing_claims(registry: Registry) -> None:
    claims = claims_by_id(registry)
    for path in public_doc_paths(registry):
        for cid, _ in CLAIM_SPAN_RE.findall(path.read_text(encoding="utf-8")):
            assert claims.get(cid, {}).get("status") == "passing", f"{path.name}: {cid} not passing"


# --------------------------------------------------------------------------------------------
# Rule 3: README numbers come from evidence
# --------------------------------------------------------------------------------------------


def test_readme_numbers_come_from_evidence(registry: Registry) -> None:
    assert README_PATH.is_file(), "README.md is missing"
    assert not lint_readme_numbers(README_PATH.read_text(encoding="utf-8"), registry)


# --------------------------------------------------------------------------------------------
# Rule 4: README claims section is generated and lists only passing claims
# --------------------------------------------------------------------------------------------


def test_readme_claims_section_is_generated_from_passing_claims(registry: Registry) -> None:
    text = README_PATH.read_text(encoding="utf-8")
    section = CLAIMS_SECTION_RE.search(text)
    assert section, "README needs <!-- claims:start --> / <!-- claims:end --> markers"
    claims = claims_by_id(registry)
    listed = [cid for cid, _ in CLAIM_SPAN_RE.findall(section.group(1))]
    assert all(claims[cid]["status"] == "passing" for cid in listed)
    renderer = _readme_renderer()
    if renderer is None:
        # Before `ssw prove --render-readme` exists nothing can be generated, so nothing is listed.
        assert not listed, "claims listed in README before the renderer exists"
    else:
        rendered = renderer(registry, EVIDENCE_DIR)
        assert section.group(0) == rendered, "run `ssw prove --render-readme`"


# --------------------------------------------------------------------------------------------
# Self-tests: the lint must catch what it claims to catch
# --------------------------------------------------------------------------------------------


def test_lint_flags_unmarked_superlatives(registry: Registry) -> None:
    for text in (
        "Stepswitch is the fastest router.",
        "Works with any open model.",
        "It never sends data anywhere.",
        "Zero setup required.",
        "This beats every other tool.",
    ):
        assert lint_public_text(text, registry), f"not flagged: {text!r}"


def test_lint_accepts_registered_wording_inside_markers(registry: Registry) -> None:
    claim = claims_by_id(registry)["C-007"]
    text = f"Privacy: <!-- claim:C-007 -->{claim['text']}<!-- /claim --> See the audit log."
    assert lint_public_text(text, registry) == []


def test_lint_rejects_altered_or_unknown_claims(registry: Registry) -> None:
    assert lint_public_text("<!-- claim:C-007 -->Sends no data.<!-- /claim -->", registry)
    assert lint_public_text("<!-- claim:C-999 -->Anything.<!-- /claim -->", registry)


def test_lint_ignores_code_and_urls(registry: Registry) -> None:
    text = (
        "Run `ssw bench --best` or see [results](https://example.org/best-zero).\n```\nnever\n```\n"
    )
    assert lint_public_text(text, registry) == []


def test_number_lint_flags_hand_typed_numbers(registry: Registry, tmp_path: Path) -> None:
    assert lint_readme_numbers("Saves 40% of tokens.", registry, tmp_path)
    assert lint_readme_numbers("Runs 3 workers in parallel.", registry, tmp_path)


def test_number_lint_accepts_rendered_and_registered_numbers(
    registry: Registry, tmp_path: Path
) -> None:
    (tmp_path / "C-011.json").write_text(
        json.dumps({"measurements": {"token_reduction_pct": 37.6}}), encoding="utf-8"
    )
    claim = claims_by_id(registry)["C-001"]["text"]
    text = (
        "Saves <!-- num:C-011:measurements.token_reduction_pct -->38%<!-- /num --> of tokens.\n"
        f"<!-- claim:C-001 -->{claim}<!-- /claim -->\nSee C-011 and SPEC §18.3, HANDOFF v0.1.\n"
    )
    assert lint_readme_numbers(text, registry, tmp_path) == []
    wrong = text.replace("38%", "45%")
    assert lint_readme_numbers(wrong, registry, tmp_path)


def test_marker_scan_finds_claim_marks(tmp_path: Path) -> None:
    tests_dir = tmp_path / "tests" / "claims"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_sample.py").write_text(
        "import pytest\n"
        "from pytest import mark\n"
        "pytestmark = [pytest.mark.claim('C-004')]\n"
        "@pytest.mark.claim('C-007')\n"
        "def test_one(): pass\n"
        "class TestGroup:\n"
        "    @mark.claim('C-016')\n"
        "    def test_two(self): pass\n"
        "def helper(): pass\n",
        encoding="utf-8",
    )
    found = marked_claim_tests(tests_dir, root=tmp_path)
    assert found == {
        "tests/claims/test_sample.py::test_one": {"C-004", "C-007"},
        "tests/claims/test_sample.py::TestGroup::test_two": {"C-004", "C-016"},
    }
