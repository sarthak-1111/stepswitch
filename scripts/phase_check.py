"""Phase acceptance checks (SPEC §23).

Usage:
    uv run python scripts/phase_check.py N            checks for phase N
    uv run python scripts/phase_check.py --upto N     checks for phases 0..N
    add --json for machine-readable output

A phase is complete only when this script exits 0 for it, `uv run pytest -q` passes, and
`uv run ssw prove --quick --phase N` reports every claim assigned to the phase as passing. The
last condition is checked here by reading claims/prove-report.json, which must carry the
current `source_digest` (see `source_digest`), so a stale report never counts.

Checks for later phases state the contracts the implementation must honour (module paths from
SPEC §21, CLI options from SPEC §19, JSON keys). Change a contract only together with an ADR.
This script never modifies the repository.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[1]
MAX_PHASE = 12
PHASE_TITLES = {
    0: "Research and design",
    1: "Foundation and compatibility core",
    2: "Built-in agent, router v1, gates, tracks",
    3: "Bench, leaderboard, compatibility matrix",
    4: "Dashboard and task API",
    5: "Token and cache optimization",
    6: "Best-of-N, disagreement, cross-family review",
    7: "Learning router, fork and replay",
    8: "Proxy and external harness adapters",
    9: "Slow lane and zero-egress",
    10: "Launch polish",
    11: "Frontier comparison",
    12: "Launch readiness",
}
SY = Path(sys.executable).with_name("ssw.exe" if os.name == "nt" else "ssw")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

CheckFn = Callable[[], tuple[bool, str]]
CHECKS: dict[int, list[tuple[str, CheckFn]]] = {phase: [] for phase in range(MAX_PHASE + 1)}


@dataclass
class Result:
    phase: int
    name: str
    ok: bool
    detail: str


def check(phase: int, name: str) -> Callable[[CheckFn], CheckFn]:
    def register(fn: CheckFn) -> CheckFn:
        CHECKS[phase].append((name, fn))
        return fn

    return register


# --------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------


def read(rel: str) -> str:
    path = ROOT / rel
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def section(text: str, heading: str) -> str:
    """Body of the first `## <heading>` section, up to the next level-2 heading."""
    pattern = re.compile(
        rf"^##[ \t]+{re.escape(heading)}[^\n]*\n(.*?)(?=^##[ \t]|\Z)", re.M | re.S | re.I
    )
    match = pattern.search(text)
    return match.group(1) if match else ""


def table_rows(text: str) -> list[list[str]]:
    """Body rows of every markdown table in `text`; header and separator rows are dropped."""
    rows: list[list[str]] = []
    in_table = False
    for line in text.splitlines():
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|")):
            in_table = False
            continue
        cells = [cell.strip() for cell in re.split(r"(?<!\\)\|", stripped[1:-1])]
        if not in_table:
            in_table = True  # header row
            continue
        if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells if cell):
            continue
        rows.append(cells)
    return rows


def run(cmd: list[str], timeout: int = 900) -> tuple[int, str, str]:
    env = {**os.environ, "NO_COLOR": "1", "TERM": "dumb", "PYTHONIOENCODING": "utf-8"}
    env["COLUMNS"] = "200"
    try:
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError:
        return 127, "", f"not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s: {' '.join(cmd)}"
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def ssw(*args: str, timeout: int = 600) -> tuple[int, str, str]:
    if not SY.exists():
        return 127, "", "the `ssw` entry point is not installed (Phase 1 adds it; then `uv sync`)"
    return run([str(SY), *args], timeout=timeout)


def last_line(text: str) -> str:
    lines = [line for line in text.strip().splitlines() if line.strip()]
    return lines[-1].strip()[:200] if lines else ""


def pytest_run(*targets: str) -> tuple[bool, str]:
    code, out, err = run(
        [sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider", *targets]
    )
    return code == 0, last_line(out) or last_line(err)


def has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def modules_check(names: Iterable[str]) -> tuple[bool, str]:
    missing = [name for name in names if not has_module(name)]
    return not missing, ("missing modules: " + ", ".join(missing)) if missing else "ok"


def help_check(args: list[str], words: Iterable[str]) -> tuple[bool, str]:
    code, out, err = ssw(*args, "--help")
    label = " ".join(["ssw", *args, "--help"])
    if code != 0:
        return False, f"`{label}` exited {code}: {last_line(err) or last_line(out)}"
    missing = [w for w in words if not re.search(rf"(?<![\w-]){re.escape(w)}(?![\w-])", out)]
    return not missing, (f"`{label}` lacks: " + ", ".join(missing)) if missing else "ok"


def tests_matching(*patterns: str) -> list[str]:
    """Patterns with no matching test file under tests/."""
    return [p for p in patterns if not any((ROOT / "tests").rglob(f"test_*{p}*.py"))]


def combine(*results: tuple[bool, str]) -> tuple[bool, str]:
    failed = [detail for ok, detail in results if not ok]
    return not failed, "; ".join(failed) if failed else "ok"


def json_out(*args: str) -> tuple[Any, str]:
    code, out, err = ssw(*args)
    if code != 0:
        return None, f"`ssw {' '.join(args)}` exited {code}: {last_line(err) or last_line(out)}"
    try:
        return json.loads(out), ""
    except json.JSONDecodeError as exc:
        return None, f"`ssw {' '.join(args)}` did not print JSON on stdout: {exc}"


# --------------------------------------------------------------------------------------------
# Claims and the prove report
# --------------------------------------------------------------------------------------------

DIGEST_SOURCES = (
    "src",
    "tests",
    "registry",
    "bench/suites",
    "bench/repos",
    "bench/golden",
    "science",
    "pyproject.toml",
    "uv.lock",
    "claims/claims.yaml",
)
DIGEST_SKIP = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules"}
DIGEST_BINARY_SUFFIXES = {".npz", ".npy", ".png", ".jpg", ".gif", ".gz", ".zip", ".gguf", ".db"}
STATUS_LINE_RE = re.compile(rb"^[ \t]*status:[^\n]*(?:\n|$)", re.M)


def source_digest(root: Path = ROOT) -> str:
    """Digest of everything a prove run depends on; `ssw prove` must record the same value.

    sha256 over files under DIGEST_SOURCES sorted by POSIX relative path; each entry is
    `path NUL sha256(content)`. Text content is hashed with CRLF normalized to LF, so a checkout
    on any OS gives the same digest. `status:` lines in claims.yaml are dropped first, because
    `ssw prove` rewrites them after computing the digest.
    """
    files: list[Path] = []
    for entry in DIGEST_SOURCES:
        path = root / entry
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(
                p
                for p in path.rglob("*")
                if p.is_file() and p.suffix != ".pyc" and not DIGEST_SKIP & set(p.parts)
            )
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda p: p.relative_to(root).as_posix()):
        rel = path.relative_to(root).as_posix()
        data = path.read_bytes()
        if path.suffix.lower() not in DIGEST_BINARY_SUFFIXES:
            data = data.replace(b"\r\n", b"\n")
        if rel == "claims/claims.yaml":
            data = STATUS_LINE_RE.sub(b"", data)
        digest.update(rel.encode() + b"\0" + hashlib.sha256(data).digest())
    return digest.hexdigest()


def load_yaml(rel: str) -> Any:
    return YAML(typ="safe", pure=True).load(read(rel) or "{}")


def load_registry() -> dict[str, Any]:
    data = load_yaml("claims/claims.yaml")
    return data if isinstance(data, dict) else {}


def claim_parts(claim: dict[str, Any]) -> list[dict[str, Any]]:
    if "parts" in claim:
        return list(claim["parts"])
    return [{"name": "", "phase": claim["phase"], "tests": claim.get("tests", [])}]


def claims_check(phase: int) -> tuple[bool, str]:
    """Every test of the claim parts assigned to `phase` passed in a fresh prove report, and every
    claim whose last part lands in `phase` is `passing` in both the report and claims.yaml.

    Report contract (claims/prove-report.json, written by `ssw prove`):
      {"schema": 1, "mode": "quick"|"full", "source_digest": "<hex>",
       "claims": {"C-0xx": {"status": "passing"|"failing"|"pending",
                            "tests": {"<node id>": "passed"|"failed"|"skipped"|"error"}}}}
    """
    registry = load_registry()
    due_tests: dict[str, str] = {}
    due_claims: list[str] = []
    for claim in registry.get("claims", []):
        parts = claim_parts(claim)
        selected = [part for part in parts if part["phase"] == phase]
        for part in selected:
            due_tests.update({node: claim["id"] for node in part["tests"]})
        if selected and max(part["phase"] for part in parts) == phase:
            due_claims.append(claim["id"])
    if not due_tests:
        return True, "no claims assigned to this phase"
    label = ", ".join(sorted(set(due_tests.values())))
    report_path = ROOT / "claims" / "prove-report.json"
    if not report_path.is_file():
        return (
            False,
            f"{label}: no claims/prove-report.json; run `uv run ssw prove --quick --phase {phase}`",
        )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("source_digest") != source_digest():
        return False, f"{label}: prove report is stale (source digest differs); re-run `ssw prove`"
    results = report.get("claims", {})
    failed = [
        f"{cid} {node.rsplit('::', 1)[-1]}"
        for node, cid in due_tests.items()
        if results.get(cid, {}).get("tests", {}).get(node) != "passed"
    ]
    status = {claim["id"]: claim.get("status") for claim in registry.get("claims", [])}
    not_passing = [
        cid
        for cid in due_claims
        if results.get(cid, {}).get("status") != "passing" or status.get(cid) != "passing"
    ]
    problems = [f"tests not passed: {', '.join(failed)}"] if failed else []
    problems += [f"claims not passing: {', '.join(not_passing)}"] if not_passing else []
    return not problems, "; ".join(problems) or f"{label} passing"


for _phase in range(1, MAX_PHASE + 1):
    check(_phase, f"claims assigned to phase {_phase} pass in a fresh `ssw prove` report")(
        lambda phase=_phase: claims_check(phase)
    )


# --------------------------------------------------------------------------------------------
# Phase 0: research and design (no product code)
# --------------------------------------------------------------------------------------------

P0_PROJECTS: dict[str, list[str]] = {
    "Big models on small hardware": ["AirLLM", "KTransformers", "llama.cpp", "bitnet.cpp"],
    "Serving and engines": [
        "vLLM",
        "SGLang",
        "Ollama",
        "MLX-LM",
        "transformers",
        "ExLlamaV3",
        "llama-swap",
        "LMCache",
        "KV-cache quantization plugins",
        "TensorRT-LLM",
    ],
    "API formats and gateways": [
        "chat-completions format",
        "responses format",
        "messages format",
        "generate-content format",
        "Ollama native API",
        "llama.cpp native API",
        "raw text-completion endpoints",
        "LiteLLM",
        "claude-code-router",
        "RouteLLM",
    ],
    "Tool-call and template formats": [
        "harmony format",
        "open-model chat templates and tool-call formats",
        "engine-side tool parsers",
    ],
    "Harnesses": [
        "Oh My Pi",
        "Pi",
        "OpenCode",
        "oh-my-openagent",
        "Kimi Code",
        "Claude Code",
        "mini-swe-agent",
        "SWE-agent",
    ],
    "Multi-agent orchestration": [
        "open-multi-agent",
        "Conductor",
        "Claude Squad",
        "Agent Client Protocol",
    ],
    "Context and tokens": [
        "Aider repo map",
        "tree-sitter",
        "LSP client libraries",
        "LLMLingua",
        "simplicio",
    ],
    "Testing and verification": [
        "pytest-testmon",
        "Hypothesis",
        "jupytext",
        "Playwright",
        "CI network isolation",
    ],
    "Name and stack": [
        "name: Switchyard",
        "uv",
        "Typer",
        "FastAPI",
        "Pydantic",
        "httpx",
        "SQLite (WAL)",
        "React",
        "Vite",
        "TypeScript",
        "ruff",
        "mypy",
        "pytest",
        "mkdocs-material",
    ],
}

# Section heading -> (minimum rows, first-column entries that must be present).
P0_TABLES: dict[str, tuple[int, list[str]]] = {
    "API formats": (
        7,
        [
            "chat-completions",
            "responses",
            "messages",
            "generate-content",
            "ollama",
            "llama.cpp",
            "text-completion",
        ],
    ),
    "Engines": (
        8,
        [
            "llama.cpp",
            "ollama",
            "vllm",
            "sglang",
            "mlx",
            "transformers",
            "ktransformers",
            "airllm",
        ],
    ),
    "Tool-call formats": (5, ["harmony"]),
    "Open-weight model families": (
        15,
        [
            "gpt-oss",
            "qwen",
            "llama",
            "gemma",
            "phi",
            "mistral",
            "deepseek",
            "glm",
            "kimi",
        ],
    ),
}

P0_ARCHITECTURE_TOPICS = [
    "orchestrator",
    "router",
    "compatibility layer",
    "model plane",
    "context services",
    "verification",
    "proxy",
    "task api",
    "dashboard",
    "state",
    "claims",
]
P0_ADR_TOPICS = {
    "name": "name",
    "stack": "stack",
    "compatibility layer": "compat",
    "proxy": "proxy",
    "router": "router",
}
ADR_FILE_RE = re.compile(r"^\d{4}-[a-z0-9-]+\.md$")


@check(0, "docs/RESEARCH.md marks every SPEC §22 project verified yes/no with a date")
def _p0_research_index() -> tuple[bool, str]:
    text = read("docs/RESEARCH.md")
    if not text:
        return False, "docs/RESEARCH.md missing"
    index: dict[str, list[str]] = {}
    for row in table_rows(section(text, "Index")):
        if len(row) >= 4:
            index[row[1].strip("*` ").lower()] = row
    problems = []
    total = 0
    for area, projects in P0_PROJECTS.items():
        for project in projects:
            total += 1
            row = index.get(project.lower())
            if row is None:
                problems.append(f"{area}/{project}: no index row")
            elif row[2].lower() not in {"yes", "no"}:
                problems.append(f"{project}: verified must be yes or no, got {row[2]!r}")
            elif not DATE_RE.match(row[3]):
                problems.append(f"{project}: checked date {row[3]!r} is not YYYY-MM-DD")
    if problems:
        return False, f"{len(problems)} problems, first: " + "; ".join(problems[:4])
    return True, f"{total} projects indexed"


@check(0, "docs/RESEARCH.md has the API-format, engine, tool-call and model-family tables")
def _p0_research_tables() -> tuple[bool, str]:
    text = read("docs/RESEARCH.md")
    problems = []
    for heading, (min_rows, required) in P0_TABLES.items():
        rows = table_rows(section(text, heading))
        firsts = [row[0].lower() for row in rows if row]
        if len(rows) < min_rows:
            problems.append(f"'{heading}': {len(rows)} rows < {min_rows}")
        missing = [item for item in required if not any(item in first for first in firsts)]
        if missing:
            problems.append(f"'{heading}' lacks rows for: {', '.join(missing)}")
    return not problems, "; ".join(problems) or "all four tables present"


@check(0, "docs/ARCHITECTURE.md covers the SPEC §3 components")
def _p0_architecture() -> tuple[bool, str]:
    text = read("docs/ARCHITECTURE.md").lower()
    if not text:
        return False, "docs/ARCHITECTURE.md missing"
    headings = "\n".join(line for line in text.splitlines() if line.startswith("#"))
    missing = [topic for topic in P0_ARCHITECTURE_TOPICS if topic not in headings]
    return not missing, ("no heading for: " + ", ".join(missing)) if missing else "ok"


@check(0, "ADRs for name, stack, compatibility layer, proxy and router")
def _p0_adrs() -> tuple[bool, str]:
    folder = ROOT / "docs" / "decisions"
    files = (
        sorted(p for p in folder.glob("*.md") if ADR_FILE_RE.match(p.name))
        if folder.is_dir()
        else []
    )
    problems = [
        f"no ADR about {topic}"
        for topic, key in P0_ADR_TOPICS.items()
        if not any(key in p.name for p in files)
    ]
    for path in files:
        body = path.read_text(encoding="utf-8")
        lacking = [h for h in ("## Context", "## Decision", "## Consequences") if h not in body]
        if not re.search(r"^(?:- |\*\*)?Status:?\**:?\s*\w+", body, re.M):
            lacking.append("Status line")
        if lacking:
            problems.append(f"{path.name} lacks {', '.join(lacking)}")
    return not problems, "; ".join(problems) or f"{len(files)} ADRs"


@check(0, "docs/PROGRESS.md records Phase 0")
def _p0_progress() -> tuple[bool, str]:
    text = read("docs/PROGRESS.md")
    ok = bool(re.search(r"^#+ .*Phase 0", text, re.M))
    return ok, "ok" if ok else "docs/PROGRESS.md missing or has no Phase 0 heading"


@check(0, "claims/claims.yaml registers C-001..C-020")
def _p0_claims_registry() -> tuple[bool, str]:
    registry = load_registry()
    claims = {claim.get("id"): claim for claim in registry.get("claims", [])}
    expected = [f"C-{n:03d}" for n in range(1, 21)]
    missing = [cid for cid in expected if cid not in claims]
    bad = [
        cid for cid, c in claims.items() if c.get("status") not in {"pending", "passing", "failing"}
    ]
    problems = ([f"missing {', '.join(missing)}"] if missing else []) + (
        [f"bad status for {', '.join(bad)}"] if bad else []
    )
    counts: dict[str, int] = {}
    for claim in claims.values():
        counts[claim.get("status", "?")] = counts.get(claim.get("status", "?"), 0) + 1
    summary = ", ".join(f"{n} {status}" for status, n in sorted(counts.items()))
    return not problems, "; ".join(problems) or summary


@check(0, "integrity meta-tests and repository hygiene tests pass")
def _p0_integrity() -> tuple[bool, str]:
    return pytest_run("tests/claims/test_integrity.py", "tests/unit/test_repo_hygiene.py")


# --------------------------------------------------------------------------------------------
# Phase 1: foundation and compatibility core
# --------------------------------------------------------------------------------------------

SPEC_COMMANDS = [
    "init",
    "doctor",
    "models",
    "probe",
    "calibrate",
    "run",
    "agent",
    "proxy",
    "bench",
    "dash",
    "replay",
    "prove",
    "policy",
    "audit",
]
FORMAT_KEYS = ["chat", "responses", "messages", "generate_content", "ollama", "llamacpp", "text"]
REGISTRY_KEYS = {
    "hub_id",
    "quant",
    "file_size_gb",
    "ram_gb",
    "context",
    "tool_calls",
    "roles",
    "license",
    "engines",
    "verified",
    "installed",
}
DOCTOR_KEYS = {"os", "cpu", "ram_gb", "gpus", "disk", "engines", "profile"}


@check(1, "package `stepswitch` and the `ssw` entry point are installed")
def _p1_package() -> tuple[bool, str]:
    problems = []
    if not (ROOT / "src" / "stepswitch" / "__init__.py").is_file():
        problems.append("src/stepswitch/__init__.py missing")
    pyproject = tomllib.loads(read("pyproject.toml") or "")
    if "ssw" not in pyproject.get("project", {}).get("scripts", {}):
        problems.append("[project.scripts] has no `ssw`")
    if not SY.exists():
        problems.append("`ssw` not installed in this environment")
    return not problems, "; ".join(problems) or "ok"


@check(1, "CLI skeleton lists every SPEC §19 command")
def _p1_cli() -> tuple[bool, str]:
    return help_check([], SPEC_COMMANDS)


@check(1, "config: `ssw init` and stepswitch.config")
def _p1_config() -> tuple[bool, str]:
    return combine(help_check(["init"], []), modules_check(["stepswitch.config"]))


@check(1, "`ssw doctor --json` reports OS, CPU, RAM, GPUs, disk, engines and a profile")
def _p1_doctor() -> tuple[bool, str]:
    data, error = json_out("doctor", "--json")
    if error:
        return False, error
    missing = DOCTOR_KEYS - set(data) if isinstance(data, dict) else DOCTOR_KEYS
    return not missing, ("missing keys: " + ", ".join(sorted(missing))) if missing else "ok"


@check(1, "registry has >= 3 T0 models, installed by `ssw models pull --tier cpu`")
def _p1_registry() -> tuple[bool, str]:
    if not list((ROOT / "registry").glob("*.toml")):
        return False, "registry/*.toml missing"
    data, error = json_out("models", "list", "--tier", "cpu", "--json")
    if error:
        return False, error
    entries = data.get("models", []) if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return False, "`ssw models list --json` must print a list (or {'models': [...]})"
    incomplete = [str(e.get("hub_id", "?")) for e in entries if REGISTRY_KEYS - set(e)]
    installed = [e for e in entries if e.get("installed") is True]
    problems = []
    if len(entries) < 3:
        problems.append(f"only {len(entries)} T0 models registered")
    if incomplete:
        problems.append(f"entries missing keys {sorted(REGISTRY_KEYS)}: {', '.join(incomplete)}")
    if len(installed) < 3:
        problems.append(
            f"only {len(installed)} T0 models installed (run `ssw models pull --tier cpu`)"
        )
    return not problems, "; ".join(problems) or f"{len(installed)} T0 models installed"


@check(1, "codecs for every SPEC §4.1 format")
def _p1_codecs() -> tuple[bool, str]:
    return modules_check(f"stepswitch.compat.codecs.{key}" for key in FORMAT_KEYS)


@check(1, "mock-server conformance tests cover every format and pass")
def _p1_conformance() -> tuple[bool, str]:
    folder = ROOT / "tests" / "conformance"
    names = " ".join(p.name for p in folder.glob("test_*.py")) if folder.is_dir() else ""
    missing = [key for key in FORMAT_KEYS if key not in names]
    if missing:
        return False, "no conformance test file for: " + ", ".join(missing)
    return pytest_run("tests/conformance")


@check(1, "llama.cpp and Ollama engine adapters and the server manager")
def _p1_engines() -> tuple[bool, str]:
    return modules_check(
        ["stepswitch.serve.manager", "stepswitch.serve.llamacpp", "stepswitch.serve.ollama"]
    )


@check(1, "`ssw models add|pull`, `ssw probe` and `ssw calibrate`")
def _p1_model_commands() -> tuple[bool, str]:
    return combine(
        help_check(["models", "add"], []),
        help_check(["models", "pull"], ["--tier", "--max-gb"]),
        help_check(["probe"], ["--export"]),
        help_check(["calibrate"], []),
        modules_check(["stepswitch.compat.probe", "stepswitch.compat.onboarding"]),
    )


@check(1, "claims framework: `ssw prove` options and the README renderer")
def _p1_prove() -> tuple[bool, str]:
    result = help_check(["prove"], ["--quick", "--full", "--phase", "--claim", "--render-readme"])
    try:
        module = importlib.import_module("stepswitch.prove.render_readme")
        renderer_ok = callable(getattr(module, "render_claims_section", None))
    except ImportError:
        renderer_ok = False
    return combine(
        result, (renderer_ok, "stepswitch.prove.render_readme.render_claims_section missing")
    )


@check(1, "integrity meta-tests pass with no skips")
def _p1_integrity_no_skips() -> tuple[bool, str]:
    code, out, err = run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-rs",
            "-p",
            "no:cacheprovider",
            "tests/claims/test_integrity.py",
        ]
    )
    if code != 0:
        return False, last_line(out) or last_line(err)
    return "skipped" not in out.lower(), last_line(out)


# --------------------------------------------------------------------------------------------
# Phase 2: built-in agent, router v1, gates, tracks
# --------------------------------------------------------------------------------------------

AGENT_TOOLS = {"read", "grep", "repo_map", "lsp", "edit", "run", "test", "search"}


@check(2, "`ssw run` options and `ssw agent`")
def _p2_cli() -> tuple[bool, str]:
    return combine(
        help_check(["run"], ["--track", "--best-of", "--offline", "--budget-tokens"]),
        help_check(["agent"], []),
    )


@check(2, "built-in agent exposes the SPEC §12 tools (TOOL_NAMES)")
def _p2_agent_tools() -> tuple[bool, str]:
    try:
        module = importlib.import_module("stepswitch.workers.builtin_agent")
    except ImportError as exc:
        return False, f"stepswitch.workers.builtin_agent: {exc}"
    missing = AGENT_TOOLS - set(getattr(module, "TOOL_NAMES", ()))
    return not missing, ("missing tools: " + ", ".join(sorted(missing))) if missing else "ok"


@check(2, "fallback tool protocol, rules + classifier routing, escalation, worktree pool")
def _p2_router() -> tuple[bool, str]:
    missing_tests = tests_matching("escalat", "worktree", "step_rout", "fallback")
    return combine(
        modules_check(
            [
                "stepswitch.compat.toolcall.fallback",
                "stepswitch.router.rules",
                "stepswitch.router.classifier",
            ]
        ),
        (not missing_tests, "no tests matching: " + ", ".join(missing_tests)),
    )


@check(2, "general and science tracks with gates, ledger and budgets")
def _p2_tracks() -> tuple[bool, str]:
    return modules_check(
        [
            "stepswitch.core.track",
            "stepswitch.core.budget",
            "stepswitch.verify.gates",
            "stepswitch.verify.science",
            "stepswitch.ledger",
        ]
    )


# --------------------------------------------------------------------------------------------
# Phase 3: bench, leaderboard, compatibility matrix
# --------------------------------------------------------------------------------------------

BENCH_TASK_KEYS = {"id", "repo", "instruction", "acceptance", "track", "tags"}


def _suite(name: str) -> list[dict[str, Any]]:
    data = load_yaml(f"bench/suites/{name}.yaml") or {}
    tasks = data.get("tasks", []) if isinstance(data, dict) else []
    return [task for task in tasks if isinstance(task, dict)]


@check(3, "bench suites: mini-general (10-20 tasks) and mini-science")
def _p3_suites() -> tuple[bool, str]:
    general, science = _suite("mini-general"), _suite("mini-science")
    problems = []
    if not 10 <= len(general) <= 20:
        problems.append(f"mini-general has {len(general)} tasks (need 10-20)")
    if len(science) < 5:
        problems.append(f"mini-science has {len(science)} tasks (need >= 5)")
    if any(task.get("track") != "science" for task in science):
        problems.append("mini-science tasks must use track: science")
    incomplete = [str(t.get("id", "?")) for t in general + science if BENCH_TASK_KEYS - set(t)]
    if incomplete:
        problems.append(
            f"tasks missing keys {sorted(BENCH_TASK_KEYS)}: {', '.join(incomplete[:5])}"
        )
    return not problems, "; ".join(problems) or f"{len(general)} general, {len(science)} science"


@check(3, "matrix runner, bootstrap CIs, fake workers and `ssw bench`")
def _p3_bench_machinery() -> tuple[bool, str]:
    try:
        metrics = importlib.import_module("stepswitch.bench.metrics")
        ci_ok = callable(getattr(metrics, "bootstrap_ci", None))
    except ImportError:
        ci_ok = False
    return combine(
        modules_check(
            ["stepswitch.bench.runner", "stepswitch.bench.report", "stepswitch.workers.fake"]
        ),
        (ci_ok, "stepswitch.bench.metrics.bootstrap_ci missing"),
        help_check(["bench", "run"], ["--suite", "--workers", "--seeds"]),
        help_check(["bench", "report"], []),
    )


@check(3, "bench/results holds results.jsonl and a leaderboard")
def _p3_results() -> tuple[bool, str]:
    folder = ROOT / "bench" / "results"
    names = {p.name for p in folder.rglob("*")} if folder.is_dir() else set()
    missing = {"results.jsonl", "leaderboard.md", "leaderboard.json"} - names
    return not missing, ("missing: " + ", ".join(sorted(missing))) if missing else "ok"


@check(3, "docs/COMPATIBILITY.md is generated from probe results")
def _p3_compatibility() -> tuple[bool, str]:
    text = read("docs/COMPATIBILITY.md")
    if "generated by `ssw probe --render-matrix`" not in text:
        return False, "docs/COMPATIBILITY.md missing or lacks the generated-file marker"
    code, out, err = ssw("probe", "--render-matrix", "--check")
    return code == 0, "matrix matches probe results" if code == 0 else (
        last_line(err) or last_line(out)
    )


@check(3, "remaining engine adapters: vLLM, SGLang, MLX, transformers")
def _p3_engines() -> tuple[bool, str]:
    return modules_check(
        f"stepswitch.serve.{name}" for name in ("vllm", "sglang", "mlx", "transformers")
    )


# --------------------------------------------------------------------------------------------
# Phase 4: dashboard and task API
# --------------------------------------------------------------------------------------------

API_ROUTES = [
    ("POST", "/api/tasks"),
    ("GET", "/api/tasks/{}"),
    ("POST", "/api/tasks/{}/cancel"),
    ("GET", "/api/workers"),
    ("GET", "/api/endpoints"),
    ("GET", "/api/leaderboard"),
    ("GET", "/api/ledger"),
    ("GET", "/api/claims"),
    ("GET", "/api/events"),
]
DASHBOARD_VIEWS = [
    "tasks",
    "agents",
    "router",
    "ledger",
    "leaderboard",
    "trajectories",
    "endpoints",
    "compatibility",
    "audit",
    "science",
    "claims",
]


@check(4, "task API routes from SPEC §17 (stepswitch.api.app.create_app)")
def _p4_api() -> tuple[bool, str]:
    try:
        app = importlib.import_module("stepswitch.api.app").create_app()
    except Exception as exc:  # noqa: BLE001 - report any import or factory failure
        return False, f"create_app(): {exc}"
    present = {
        (method, re.sub(r"\{[^}]+\}", "{}", getattr(route, "path", "")))
        for route in getattr(app, "routes", [])
        for method in (getattr(route, "methods", None) or set())
    }
    missing = [f"{m} {p}" for m, p in API_ROUTES if (m, p) not in present]
    return not missing, ("missing routes: " + ", ".join(missing)) if missing else "ok"


@check(4, "dashboard has a route for every SPEC §17 view and a built bundle")
def _p4_dashboard() -> tuple[bool, str]:
    src = ROOT / "dashboard" / "src"
    if not src.is_dir():
        return False, "dashboard/src missing"
    text = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in src.rglob("*.ts*"))
    missing = [view for view in DASHBOARD_VIEWS if not re.search(rf"['\"`]/{view}['\"`/]", text)]
    built = (ROOT / "dashboard" / "dist" / "index.html").is_file()
    return combine(
        (not missing, "no route for views: " + ", ".join(missing)),
        (built, "dashboard/dist/index.html missing (cd dashboard && npm ci && npm run build)"),
    )


@check(4, "Playwright smoke test for the dashboard")
def _p4_playwright() -> tuple[bool, str]:
    found = list((ROOT / "tests").rglob("test_*dashboard*.py")) + [
        path for path in (ROOT / "dashboard").rglob("*.spec.ts") if "node_modules" not in path.parts
    ]
    return bool(found), "ok" if found else "no Playwright smoke test found"


@check(4, "README claims section matches `ssw prove --render-readme`")
def _p4_readme() -> tuple[bool, str]:
    code, out, err = ssw("prove", "--render-readme", "--check")
    return code == 0, "README is current" if code == 0 else (last_line(err) or last_line(out))


# --------------------------------------------------------------------------------------------
# Phase 5: token and cache optimization
# --------------------------------------------------------------------------------------------

HANDOFF_SPEC = "spec/handoff-v0.1.md"
HANDOFF_SCHEMA = "spec/handoff-v0.1.schema.json"
# SPEC §9: the keys of the brief and of each nested object ("[]" marks array items).
HANDOFF_FIELDS: dict[str, set[str]] = {
    "": {
        "version",
        "task_id",
        "goal",
        "track",
        "constraints",
        "acceptance",
        "state",
        "files",
        "failing",
        "budget_left",
        "provenance",
    },
    "acceptance": {"commands", "golden"},
    "state": {"done", "decisions", "open_questions"},
    "state.decisions[]": {"what", "why"},
    "files[]": {"path", "symbols", "why"},
    "failing[]": {"check", "excerpt"},
    "budget_left": {"tokens", "seconds"},
    "provenance": {"from_worker", "step", "tokenizer", "exact"},
}
# SPEC §9 tokenizer resolution order (`stepswitch.context.tokens.RESOLUTION_ORDER`).
TOKENIZER_ORDER = ("tokenizer.json", "server", "mapped", "estimate")
# SPEC §13: the modules of each technique and name fragments of the tests that cover it.
TK_PARTS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "TK1": (("stepswitch.context.prefix",), ("stable_prefix",)),
    "TK2": (
        ("stepswitch.context.repomap", "stepswitch.context.treesitter"),
        ("repomap", "treesitter"),
    ),
    "TK3": (("stepswitch.context.dedup",), ("read_dedup",)),
    "TK4": (("stepswitch.context.distill",), ("distill",)),
    "TK5": (("stepswitch.context.edits",), ("context_edits",)),
    "TK6": (("stepswitch.context.prune",), ("prune",)),
    "TK7": (("stepswitch.context.handoff",), ("handoff",)),
    "TK8": (
        (
            "stepswitch.router.classifier",
            "stepswitch.compat.toolcall.fallback",
            "stepswitch.context.handoff",
        ),
        ("constrained",),
    ),
    "TK9": (("stepswitch.context.search",), ("context_search",)),
    "TK10": (("stepswitch.router.budgets",), ("role_budget",)),
    "TK11": (("stepswitch.context.compress",), ("compress",)),
    "TK12": (("stepswitch.ledger",), ("ledger",)),
}
# Languages whose definitions and references come from tree-sitter grammar wheels (TK2).
TREE_SITTER_LANGUAGES = {"javascript", "typescript", "go", "rust", "java", "c", "cpp", "ruby"}
SUITES = ("mini-general", "mini-science")


def _schema_properties(schema: dict[str, Any]) -> dict[str, set[str]]:
    """Property names per object path of a JSON Schema ("[]" marks array items)."""
    found: dict[str, set[str]] = {}

    def walk(node: Any, path: str) -> None:
        if not isinstance(node, dict):
            return
        props = node.get("properties")
        if isinstance(props, dict):
            found[path] = set(props)
            for name, child in props.items():
                walk(child, f"{path}.{name}" if path else name)
        if isinstance(node.get("items"), dict):
            walk(node["items"], f"{path}[]")

    walk(schema, "")
    return found


@check(5, "HANDOFF v0.1: spec, JSON Schema with the SPEC §9 fields, and a valid example")
def _p5_handoff_spec() -> tuple[bool, str]:
    text = read(HANDOFF_SPEC)
    raw = read(HANDOFF_SCHEMA)
    if not text or not raw:
        return False, f"{HANDOFF_SPEC} and {HANDOFF_SCHEMA} must exist"
    try:
        from jsonschema import Draft202012Validator

        schema = json.loads(raw)
        Draft202012Validator.check_schema(schema)
    except Exception as exc:  # noqa: BLE001 - an unreadable schema fails the check
        return False, f"{HANDOFF_SCHEMA}: {exc}"
    present = _schema_properties(schema)
    problems = [
        f"{path or 'brief'} lacks {', '.join(sorted(keys - present.get(path, set())))}"
        for path, keys in HANDOFF_FIELDS.items()
        if keys - present.get(path, set())
    ]
    if set(schema.get("required", [])) != HANDOFF_FIELDS[""]:
        problems.append("every top-level field must be required")
    examples = re.findall(r"```json\n(.*?)```", text, re.S)
    if not examples:
        problems.append(f"{HANDOFF_SPEC} has no JSON example")
    for example in examples:
        try:
            errors = list(Draft202012Validator(schema).iter_errors(json.loads(example)))
        except json.JSONDecodeError as exc:
            problems.append(f"example is not JSON: {exc}")
            continue
        problems += [f"example: {error.message}" for error in errors[:3]]
    return not problems, "; ".join(problems) or f"{len(examples)} example(s) validate"


@check(5, "tokenizer resolution follows SPEC §9 (stepswitch.context.tokens)")
def _p5_tokens() -> tuple[bool, str]:
    try:
        tokens = importlib.import_module("stepswitch.context.tokens")
    except ImportError as exc:
        return False, f"stepswitch.context.tokens: {exc}"
    order = tuple(getattr(tokens, "RESOLUTION_ORDER", ()))
    missing = [name for name in ("resolve", "TokenCount") if not hasattr(tokens, name)]
    return combine(
        (order == TOKENIZER_ORDER, f"RESOLUTION_ORDER is {order}, expected {TOKENIZER_ORDER}"),
        (not missing, "missing: " + ", ".join(missing)),
    )


@check(5, "TK1-TK12: a module and tests for each technique (SPEC §13)")
def _p5_techniques() -> tuple[bool, str]:
    problems = []
    for tk, (modules, tests) in TK_PARTS.items():
        missing = [name for name in modules if not has_module(name)]
        untested = tests_matching(*tests)
        if missing:
            problems.append(f"{tk} modules missing: {', '.join(missing)}")
        if untested:
            problems.append(f"{tk} no tests matching: {', '.join(untested)}")
    return not problems, "; ".join(problems) or f"{len(TK_PARTS)} techniques"


@check(5, "cache-aware switching: prefix registry, HANDOFF threshold, learned handoff penalty")
def _p5_switching() -> tuple[bool, str]:
    try:
        cost = importlib.import_module("stepswitch.router.cost")
        cache = importlib.import_module("stepswitch.router.cache")
        calibration = importlib.import_module("stepswitch.router.calibration")
    except ImportError as exc:
        return False, str(exc)
    threshold = getattr(cost, "HANDOFF_THRESHOLD_TOKENS", None)
    untested = tests_matching("prefix_cache", "switch_cost", "router_calibration")
    return combine(
        (threshold == 24_000, f"HANDOFF_THRESHOLD_TOKENS is {threshold}, SPEC §7.2 says 24000"),
        (hasattr(cache, "PrefixRegistry"), "stepswitch.router.cache.PrefixRegistry missing"),
        (
            hasattr(calibration, "HandoffRecovery"),
            "stepswitch.router.calibration.HandoffRecovery missing",
        ),
        (not untested, "no tests matching: " + ", ".join(untested)),
    )


@check(5, "calibration commands: `ssw calibrate --cache` and `ssw bench calibrate`")
def _p5_calibrate() -> tuple[bool, str]:
    return combine(
        help_check(["calibrate"], ["--cache"]),
        help_check(["bench", "calibrate"], []),
        help_check(["bench", "run"], ["--baseline"]),
    )


@check(5, "tree-sitter grammars for the core languages load offline")
def _p5_treesitter() -> tuple[bool, str]:
    try:
        treesitter = importlib.import_module("stepswitch.context.treesitter")
        available = set(treesitter.available_languages())
    except Exception as exc:  # noqa: BLE001 - report import or grammar failures
        return False, f"stepswitch.context.treesitter: {exc}"
    missing = TREE_SITTER_LANGUAGES - available
    return not missing, ("no grammar for: " + ", ".join(sorted(missing))) if missing else "ok"


@check(5, "registry models that drive agent turns declare an agent context size")
def _p5_agent_context() -> tuple[bool, str]:
    problems = []
    for path in sorted((ROOT / "registry").glob("*.toml")):
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        model = data.get("model", {})
        roles = set(model.get("roles", []))
        if not roles or roles <= {"embeddings", "search_ranking", "fim"}:
            continue
        agent_ctx = model.get("agent_ctx")
        if not isinstance(agent_ctx, int) or agent_ctx < 8192:
            problems.append(f"{path.name}: agent_ctx {agent_ctx!r} (need an integer >= 8192)")
    return not problems, "; ".join(problems) or "ok"


@check(5, "bench/results holds optimized and baseline runs of both mini suites")
def _p5_bench_runs() -> tuple[bool, str]:
    found: dict[tuple[str, bool], int] = {}
    for run_file in (ROOT / "bench" / "results").glob("*/run.json"):
        data = json.loads(run_file.read_text(encoding="utf-8"))
        if "optimize" in data:
            key = (str(data.get("suite")), bool(data["optimize"]))
            found[key] = found.get(key, 0) + 1
    missing = [
        f"{suite} ({'optimized' if flag else 'baseline'})"
        for suite in SUITES
        for flag in (True, False)
        if not found.get((suite, flag))
    ]
    return not missing, ("missing runs: " + ", ".join(missing)) if missing else "ok"


# --------------------------------------------------------------------------------------------
# Phase 6: best-of-N, disagreement, cross-family review
# --------------------------------------------------------------------------------------------

# SPEC §8.3: candidates per classifier difficulty (d <= 2 -> 1, d = 3 -> 2, d >= 4 -> 3).
BEST_OF_N = {1: 1, 2: 1, 3: 2, 4: 3, 5: 3}
# SPEC §8.5: the review answer and each of its issues ("[]" marks array items).
REVIEW_FIELDS: dict[str, set[str]] = {
    "": {"approve", "issues"},
    "issues[]": {"severity", "file", "line", "note"},
}
# SPEC §8.4: what happens when passing candidates disagree.
DISAGREEMENT_POLICIES = {"flag", "tests", "escalate", "human"}
RESULT_FIELDS = {"best_of", "candidates", "winner", "disagreement", "uncertain"}
BEST_OF_N_CONTESTANT = "best-of-n"


@check(6, "best-of-N: candidates per difficulty as in SPEC §8.3, family plan and selection")
def _p6_best_of_n() -> tuple[bool, str]:
    try:
        module = importlib.import_module("stepswitch.verify.best_of_n")
    except ImportError as exc:
        return False, f"stepswitch.verify.best_of_n: {exc}"
    count = getattr(module, "candidate_count", None)
    wrong = {d: count(d) for d in BEST_OF_N if count(d) != BEST_OF_N[d]} if callable(count) else {}
    missing = [name for name in ("plan_candidates", "select") if not hasattr(module, name)]
    untested = tests_matching("best_of_n")
    return combine(
        (callable(count), "candidate_count(difficulty) missing"),
        (not wrong, f"candidate_count differs from SPEC §8.3: {wrong}"),
        (not missing, "missing: " + ", ".join(missing)),
        (not untested, "no tests matching: " + ", ".join(untested)),
    )


@check(6, "cross-family review answers the SPEC §8.5 schema")
def _p6_review() -> tuple[bool, str]:
    try:
        module = importlib.import_module("stepswitch.verify.review")
    except ImportError as exc:
        return False, f"stepswitch.verify.review: {exc}"
    schema = getattr(module, "REVIEW_SCHEMA", None)
    if not isinstance(schema, dict):
        return False, "stepswitch.verify.review.REVIEW_SCHEMA missing"
    present = _schema_properties(schema)
    problems = [
        f"{path or 'review'} lacks {', '.join(sorted(keys - present.get(path, set())))}"
        for path, keys in REVIEW_FIELDS.items()
        if keys - present.get(path, set())
    ]
    untested = tests_matching("cross_review")
    problems += ["no tests matching: " + ", ".join(untested)] if untested else []
    return not problems, "; ".join(problems) or "ok"


@check(6, "disagreement signal: test and science comparisons, the SPEC §8.4 policies")
def _p6_disagreement() -> tuple[bool, str]:
    try:
        module = importlib.import_module("stepswitch.verify.disagreement")
    except ImportError as exc:
        return False, f"stepswitch.verify.disagreement: {exc}"
    policies = set(getattr(module, "POLICIES", ()))
    missing = [name for name in ("analyze", "compare_outputs") if not hasattr(module, name)]
    untested = tests_matching("disagreement")
    return combine(
        (policies == DISAGREEMENT_POLICIES, f"POLICIES is {sorted(policies)}"),
        (not missing, "missing: " + ", ".join(missing)),
        (not untested, "no tests matching: " + ", ".join(untested)),
    )


@check(6, "CLI: `ssw run --best-of --on-disagreement`, bench contestant best-of-n")
def _p6_cli() -> tuple[bool, str]:
    return combine(
        help_check(["run"], ["--best-of", "--on-disagreement"]),
        help_check(["bench", "run"], ["--best-of", BEST_OF_N_CONTESTANT]),
    )


@check(6, "task results carry the candidates, the winner and the disagreement report")
def _p6_result() -> tuple[bool, str]:
    try:
        task = importlib.import_module("stepswitch.core.task")
    except ImportError as exc:
        return False, str(exc)
    fields = set(getattr(task.TaskResult, "model_fields", {}))
    missing = sorted(RESULT_FIELDS - fields)
    return not missing, ("TaskResult lacks: " + ", ".join(missing)) if missing else "ok"


@check(6, "bench/results holds best-of-N runs with every pool worker as a single contestant")
def _p6_bench_runs() -> tuple[bool, str]:
    found: set[str] = set()
    for run_file in (ROOT / "bench" / "results").glob("*/run.json"):
        data = json.loads(run_file.read_text(encoding="utf-8"))
        contestants = list(data.get("contestants", []))
        pool = list(data.get("router_pool") or [])
        if BEST_OF_N_CONTESTANT in contestants and pool and set(pool) <= set(contestants):
            found.add(str(data.get("suite")))
    missing = [suite for suite in SUITES if suite not in found]
    return not missing, ("missing runs: " + ", ".join(missing)) if missing else "ok"


# --------------------------------------------------------------------------------------------
# Phase 7: learning router, fork and replay
# --------------------------------------------------------------------------------------------

# Policies the simulation harness compares (SPEC §10; claim C-015).
SIMULATION_POLICIES = {"thompson", "random", "fixed"}


@check(7, "learning router: contextual Thompson sampling with the SPEC §10 reward")
def _p7_learning() -> tuple[bool, str]:
    try:
        module = importlib.import_module("stepswitch.router.learning")
    except ImportError as exc:
        return False, f"stepswitch.router.learning: {exc}"
    missing = [
        name
        for name in ("Bandit", "LearningSettings", "reward", "context_keys")
        if not hasattr(module, name)
    ]
    if missing:
        return False, "missing: " + ", ".join(missing)
    settings = module.LearningSettings()
    best = module.reward(True, 0.0, 0, settings)
    worst = module.reward(False, 10**9, 10**12, settings)
    expected_worst = -(settings.lambda_time + settings.mu_tokens)
    untested = tests_matching("learning_router")
    return combine(
        (best == 1.0, f"reward of a free success is {best}, not 1"),
        (
            abs(worst - expected_worst) < 1e-9,
            f"reward of a costly failure is {worst}, not -(lambda + mu) = {expected_worst}",
        ),
        (not untested, "no tests matching: " + ", ".join(untested)),
    )


@check(7, "simulation harness with synthetic workers: Thompson sampling, random and fixed")
def _p7_simulation() -> tuple[bool, str]:
    try:
        module = importlib.import_module("stepswitch.router.simulate")
    except ImportError as exc:
        return False, f"stepswitch.router.simulate: {exc}"
    policies = set(getattr(module, "POLICIES", ()))
    untested = tests_matching("simulate")
    return combine(
        (callable(getattr(module, "simulate", None)), "simulate() missing"),
        (policies >= SIMULATION_POLICIES, f"POLICIES is {sorted(policies)}"),
        (not untested, "no tests matching: " + ", ".join(untested)),
    )


@check(7, "trajectories record every model answer; `ssw replay` replays and forks runs")
def _p7_replay() -> tuple[bool, str]:
    try:
        module = importlib.import_module("stepswitch.trajectory.replay")
    except ImportError as exc:
        return False, f"stepswitch.trajectory.replay: {exc}"
    missing = [n for n in ("load_recording", "replay_run") if not hasattr(module, n)]
    cli = (ROOT / "src" / "stepswitch" / "cli.py").read_text(encoding="utf-8")
    untested = tests_matching("replay")
    return combine(
        (not missing, "missing: " + ", ".join(missing)),
        ('_not_yet("replay"' not in cli, "`ssw replay` is still a stub"),
        help_check(["replay"], ["--from-step", "--worker"]),
        (not untested, "no tests matching: " + ", ".join(untested)),
    )


@check(7, "CLI: `ssw router stats` shows what the router learned; `--export` writes aggregates")
def _p7_router_cli() -> tuple[bool, str]:
    return help_check(["router", "stats"], ["--export"])


# --------------------------------------------------------------------------------------------
# Phase 8: proxy and external harness adapters
# --------------------------------------------------------------------------------------------

# Inbound routes of ADR 0004 item 2 (the responses WebSocket mode is deferred there).
PROXY_ROUTES = {
    "/v1/chat/completions",
    "/v1/completions",
    "/v1/responses",
    "/v1/messages",
    "/v1/messages/count_tokens",
    "/v1beta/models/{model}:generateContent",
    "/v1beta/models/{model}:streamGenerateContent",
    "/api/chat",
    "/api/generate",
    "/api/tags",
    "/api/show",
    "/v1/models",
}
# SPEC §12 items 3 to 7: one adapter per harness, plus the generic ACP client.
HARNESS_ADAPTERS = {"claude-code", "pi", "oh-my-pi", "opencode", "kimi-code", "acp"}
# Adapters of TypeScript harnesses that ship an extension package (CLAUDE.md, Stack).
TS_ADAPTERS = ("pi", "omp")  # adapters/<dir> (SPEC §21)
FIXTURES = ROOT / "tests" / "fixtures" / "harness"


@check(8, "`ssw proxy` serves every inbound format of ADR 0004 on loopback with a local token")
def _p8_proxy() -> tuple[bool, str]:
    try:
        module = importlib.import_module("stepswitch.proxy.app")
    except ImportError as exc:
        return False, f"stepswitch.proxy.app: {exc}"
    routes = set(getattr(module, "INBOUND_ROUTES", ()))
    missing = sorted(PROXY_ROUTES - routes)
    cli = (ROOT / "src" / "stepswitch" / "cli.py").read_text(encoding="utf-8")
    untested = tests_matching("proxy")
    return combine(
        (callable(getattr(module, "create_proxy_app", None)), "create_proxy_app() missing"),
        (not missing, "routes missing: " + ", ".join(missing)),
        (getattr(module, "HOST", None) == "127.0.0.1", "the proxy does not bind to 127.0.0.1"),
        (getattr(module, "DEFAULT_PORT", None) == 7778, "the default port is not 7778"),
        ('_not_yet("proxy"' not in cli, "`ssw proxy` is still a stub"),
        help_check(["proxy"], ["--port"]),
        (not untested, "no tests matching: " + ", ".join(untested)),
    )


@check(8, "proxy: models switch only at a new user turn; each request is a recorded step")
def _p8_proxy_turns() -> tuple[bool, str]:
    try:
        module = importlib.import_module("stepswitch.proxy.sessions")
    except ImportError as exc:
        return False, f"stepswitch.proxy.sessions: {exc}"
    missing = [n for n in ("SessionStore", "user_turn", "session_key") if not hasattr(module, n)]
    if missing:
        return False, "missing: " + ", ".join(missing)
    untested = tests_matching("proxy_sessions")
    if untested:
        return False, "no tests matching: " + ", ".join(untested)
    return pytest_run("tests/unit/test_proxy_sessions.py")


@check(8, "harness adapters with the SPEC §12 interface, one per harness and a generic ACP client")
def _p8_adapters() -> tuple[bool, str]:
    try:
        module = importlib.import_module("stepswitch.workers.harness")
    except ImportError as exc:
        return False, f"stepswitch.workers.harness: {exc}"
    adapters = getattr(module, "ADAPTERS", {})
    missing = sorted(HARNESS_ADAPTERS - set(adapters))
    interface = [
        f"{cls}.{name}"
        for cls, names in (
            ("HarnessAdapter", ("start",)),
            ("RunHandle", ("events", "cancel", "result")),
        )
        for name in names
        if not hasattr(getattr(module, cls, None), name)
    ]
    no_offline = sorted(
        name for name, adapter in adapters.items() if not getattr(adapter, "offline_env", None)
    )
    packages = [
        name for name in TS_ADAPTERS if not (ROOT / "adapters" / name / "package.json").is_file()
    ]
    return combine(
        (not missing, "adapters missing: " + ", ".join(missing)),
        (not interface, "interface lacks: " + ", ".join(interface)),
        (not no_offline, "adapters without offline egress switches: " + ", ".join(no_offline)),
        (not packages, "TypeScript adapter packages missing: " + ", ".join(packages)),
    )


@check(8, "contract tests with recorded fixtures for every adapter")
def _p8_contracts() -> tuple[bool, str]:
    problems = []
    for name in sorted(HARNESS_ADAPTERS):
        meta_file = FIXTURES / name / "fixture.json"
        if not meta_file.is_file():
            problems.append(f"{name}: no fixture.json")
            continue
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        if meta.get("source") not in {"recorded", "documented"}:
            problems.append(f"{name}: source is {meta.get('source')!r}")
        if not DATE_RE.match(str(meta.get("checked", ""))):
            problems.append(f"{name}: no checked date")
        if not any((FIXTURES / name).glob("*.jsonl")):
            problems.append(f"{name}: no recorded transcript (*.jsonl)")
    if tests_matching("harness_contract"):
        problems.append("no tests matching: harness_contract")
    if problems:
        return False, "; ".join(problems)
    return pytest_run("tests/unit/test_harness_contract.py")


@check(8, "a live end-to-end run through `ssw proxy` with an installed harness")
def _p8_live() -> tuple[bool, str]:
    live = []
    for meta_file in sorted(FIXTURES.glob("*/fixture.json")):
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        run = meta.get("live") or {}
        if meta.get("source") == "recorded" and run.get("status") == "done":
            live.append(f"{meta_file.parent.name} {meta.get('version')}")
    return bool(live), ("live runs: " + ", ".join(live)) if live else "no recorded live run"


# --------------------------------------------------------------------------------------------
# Phase 9: slow lane and zero-egress
# --------------------------------------------------------------------------------------------

SLOW_ENGINES = {"airllm", "ktransformers"}
JOB_COMMANDS = ["submit", "list", "show", "run", "cancel"]
JOB_FUNCTIONS = ("submit", "claim", "finish", "cancel", "requeue_stale", "run_queue")
JOB_ROUTES = [("GET", "/api/jobs"), ("GET", "/api/jobs/{}")]
# Kinds the redactor must recognise (ADR 0031): keys, tokens and credentials in URLs and headers.
REDACTED_KINDS = {
    "private-key",
    "api-key",
    "repo-token",
    "package-token",
    "cloud-key-id",
    "jwt",
    "bearer",
    "basic",
    "url-password",
}
# Commands run while offline get the offline switches of these tools (stepswitch.context.proc).
OFFLINE_SWITCHES = {"UV_OFFLINE", "PIP_NO_INDEX", "HF_HUB_OFFLINE", "npm_config_offline"}
# Where the per-path deny list is applied, per harness (ADR 0031).
DENY_HOOKS = {
    "built-in tools": ("src/stepswitch/workers/tools.py", "DenyList.from_config(config)"),
    "acp": ("src/stepswitch/workers/acp.py", "DenyList(settings.deny_paths)"),
    "claude-code": ("src/stepswitch/workers/claude_code.py", "deny_rules(settings.deny_paths)"),
    "opencode": ("src/stepswitch/workers/opencode.py", "deny_rules(tuple(settings.deny_paths))"),
    "pi": ("adapters/pi/index.js", "guardDeniedPaths(pi, process.env)"),
    "oh-my-pi": ("adapters/omp/index.js", "guardDeniedPaths(pi, process.env)"),
}


def api_routes() -> tuple[set[tuple[str, str]] | None, str]:
    try:
        app = importlib.import_module("stepswitch.api.app").create_app()
    except Exception as exc:  # noqa: BLE001 - report any import or factory failure
        return None, f"create_app(): {exc}"
    present = {
        (method, re.sub(r"\{[^}]+\}", "{}", getattr(route, "path", "")))
        for route in getattr(app, "routes", [])
        for method in (getattr(route, "methods", None) or set())
    }
    return present, ""


def untested_or_run(patterns: tuple[str, ...], *targets: str) -> tuple[bool, str]:
    untested = tests_matching(*patterns)
    if untested:
        return False, "no tests matching: " + ", ".join(untested)
    return pytest_run(*targets)


@check(9, "slow lane: AirLLM and KTransformers backends for pin-only SL-tier workers")
def _p9_slow_engines() -> tuple[bool, str]:
    try:
        manager = importlib.import_module("stepswitch.serve.manager")
        base = importlib.import_module("stepswitch.workers.base")
    except ImportError as exc:
        return False, str(exc)
    engines = {getattr(cls, "name", None) for cls in getattr(manager, "PYTHON_ENGINES", ())}
    missing = sorted(SLOW_ENGINES - engines)
    tier = getattr(base, "SLOW_TIER", None)
    problems = combine(
        (not missing, "engines missing from the server manager: " + ", ".join(missing)),
        (tier == "sl", "no slow-lane tier (SLOW_TIER) in stepswitch.workers.base"),
        (tier in getattr(base, "LEVEL_BY_TIER", {}), "the SL tier has no escalation level"),
        (has_module("stepswitch.serve.airllm_server"), "the AirLLM server shim is missing"),
    )
    if not problems[0]:
        return problems
    return untested_or_run(
        ("serve_airllm", "serve_ktransformers"),
        "tests/unit/test_serve_airllm.py",
        "tests/unit/test_serve_ktransformers.py",
    )


@check(9, "asynchronous jobs: `ssw jobs`, `ssw run --slow-review`, review comments, /api/jobs")
def _p9_jobs() -> tuple[bool, str]:
    try:
        jobs = importlib.import_module("stepswitch.core.jobs")
    except ImportError as exc:
        return False, f"stepswitch.core.jobs: {exc}"
    missing = [name for name in JOB_FUNCTIONS if not callable(getattr(jobs, name, None))]
    routes, error = api_routes()
    absent = [f"{m} {p}" for m, p in JOB_ROUTES if routes is None or (m, p) not in routes]
    problems = combine(
        (not missing, "stepswitch.core.jobs lacks: " + ", ".join(missing)),
        (routes is not None, error),
        (routes is None or not absent, "routes missing: " + ", ".join(absent)),
        help_check(["jobs"], JOB_COMMANDS),
        help_check(["jobs", "submit"], ["--worker", "--task", "--json"]),
        help_check(["run"], ["--slow-review"]),
    )
    if not problems[0]:
        return problems
    return untested_or_run(("jobs",), "tests/unit/test_jobs.py")


@check(9, "offline mode: per-task enforcement, offline child commands, forwarding endpoints")
def _p9_offline() -> tuple[bool, str]:
    try:
        proc = importlib.import_module("stepswitch.context.proc")
        forwarding = importlib.import_module("stepswitch.compat.forwarding")
    except ImportError as exc:
        return False, str(exc)
    switches = sorted(OFFLINE_SWITCHES - set(getattr(proc, "OFFLINE_ENV", {})))
    helpers = [
        name
        for name in ("static_reason", "offline_refusal", "llama_swap_forwarding")
        if not callable(getattr(forwarding, name, None))
    ]
    problems = combine(
        (not switches, "offline switches missing: " + ", ".join(switches)),
        (not helpers, "stepswitch.compat.forwarding lacks: " + ", ".join(helpers)),
        help_check(["run"], ["--offline"]),
    )
    if not problems[0]:
        return problems
    return untested_or_run(
        ("offline_mode", "forwarding", "context_offline"),
        "tests/unit/test_offline_mode.py",
        "tests/unit/test_forwarding.py",
        "tests/unit/test_context_offline.py",
    )


@check(9, "audit: every connection (with bytes) and every spawned process; `ssw audit`")
def _p9_audit() -> tuple[bool, str]:
    try:
        net = importlib.import_module("stepswitch.net")
        state = importlib.import_module("stepswitch.state")
    except ImportError as exc:
        return False, str(exc)
    schema = " ".join(getattr(state, "MIGRATIONS", ()))
    fields = set(getattr(getattr(net, "AuditRow", None), "__dataclass_fields__", {}))
    problems = combine(
        ("process_audit" in schema, "no process_audit table in the state schema"),
        ({"bytes_out", "bytes_in"} <= fields, "AuditRow does not record bytes in and out"),
        (callable(getattr(net, "audit_processes", None)), "stepswitch.net.audit_processes missing"),
        help_check(["audit"], ["--processes", "--task", "--blocked", "--json"]),
    )
    if not problems[0]:
        return problems
    return untested_or_run(("audit",), "tests/unit/test_audit.py")


@check(9, "redaction: secrets are masked in requests to non-local endpoints")
def _p9_redaction() -> tuple[bool, str]:
    try:
        redact = importlib.import_module("stepswitch.redact")
    except ImportError as exc:
        return False, f"stepswitch.redact: {exc}"
    kinds = {pattern[0] for pattern in getattr(redact, "PATTERNS", ())}
    missing = sorted(REDACTED_KINDS - kinds)
    problems = combine(
        (not missing, "secret kinds not recognised: " + ", ".join(missing)),
        (getattr(redact, "MODES", ()) == ("remote", "all", "off"), "redaction modes changed"),
    )
    if not problems[0]:
        return problems
    return untested_or_run(("redact",), "tests/unit/test_redact.py")


@check(9, "privacy deny lists reach every harness: built-in tools, ACP, Claude Code, OpenCode, Pi")
def _p9_deny_lists() -> tuple[bool, str]:
    missing = [
        f"{name} ({rel})" for name, (rel, marker) in DENY_HOOKS.items() if marker not in read(rel)
    ]
    if missing:
        return False, "no deny-list hook in: " + ", ".join(missing)
    return untested_or_run(("deny_paths",), "tests/unit/test_deny_paths.py")


@check(9, "`ssw prefetch` fetches what context services use, so offline runs download nothing")
def _p9_prefetch() -> tuple[bool, str]:
    try:
        prefetch = importlib.import_module("stepswitch.prefetch")
    except ImportError as exc:
        return False, f"stepswitch.prefetch: {exc}"
    services = set(getattr(prefetch, "SERVICES", ()))
    wanted = {"tree-sitter", "tokenizers", "language servers", "search"}
    problems = combine(
        (wanted <= services, "services not covered: " + ", ".join(sorted(wanted - services))),
        help_check(["prefetch"], ["--check", "--json"]),
    )
    if not problems[0]:
        return problems
    return untested_or_run(("prefetch",), "tests/unit/test_prefetch.py")


RESEARCH_P9_TOPICS = ("AirLLM", "KTransformers", "llama-swap", "deny rules", "Secret patterns")


@check(9, "ADRs 0030 and 0031, the Phase 9 research notes and the jobs API are written down")
def _p9_docs() -> tuple[bool, str]:
    decisions = ROOT / "docs" / "decisions"
    adrs = [n for n in ("0030", "0031") if not any(decisions.glob(f"{n}-*.md"))]
    research = section(read("docs/RESEARCH.md"), "Verified during Phase 9")
    topics = [topic for topic in RESEARCH_P9_TOPICS if topic not in research]
    api = read("docs/API.md")
    return combine(
        (not adrs, "ADRs missing: " + ", ".join(adrs)),
        (bool(research), "docs/RESEARCH.md has no `## Verified during Phase 9` section"),
        (not research or not topics, "the Phase 9 research notes lack: " + ", ".join(topics)),
        ("/api/jobs" in api, "docs/API.md does not document /api/jobs"),
    )


@check(9, "CI runs the offline claim under OS-level network isolation on Linux")
def _p9_ci_isolation() -> tuple[bool, str]:
    workflow = read(".github/workflows/prove.yml")
    egress = read("tests/claims/egress.py")
    return combine(
        (bool(workflow), ".github/workflows/prove.yml missing"),
        ("ubuntu-24.04" in workflow, "no pinned Linux runner"),
        (
            "kernel.apparmor_restrict_unprivileged_userns=0" in workflow,
            "the Linux job does not allow unprivileged network namespaces",
        ),
        ("C-006" in workflow, "the workflow does not name C-006 next to its isolation step"),
        ("unshare" in egress and "--net" in egress, "tests/claims/egress.py does not isolate"),
    )


# --------------------------------------------------------------------------------------------
# Phase 10: launch polish
# --------------------------------------------------------------------------------------------

# The quickstart, in order (SPEC §24 packaging; C-017 times it).
QUICKSTART = ("install.sh", "install.ps1", "ssw init", "ssw models pull --tier cpu", "ssw run")
COMMUNITY_FILES = (
    "LICENSE",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "CODE_OF_CONDUCT.md",
    ".github/pull_request_template.md",
)
ISSUE_FORM_KEYS = {"name", "description", "body"}
PINNED_ACTION_RE = re.compile(r"^\s*-?\s*uses:\s*[\w.-]+/[\w./-]+@([0-9a-f]{40})\b")
ANY_ACTION_RE = re.compile(r"^\s*-?\s*uses:\s*(\S+)")
# What each installer must contain: the pinned uv and the check of what it downloads, the
# prefetch and the package override. install.sh runs uv's installer, so it also opts out of
# that installer's profile edits; install.ps1 unpacks uv's release archive itself.
INSTALLER_NEEDLES = {
    "install.sh": (
        'UV_VERSION="',
        "UV_INSTALLER_SHA256=",
        "UV_NO_MODIFY_PATH=1",
        "ssw prefetch",
        "STEPSWITCH_PACKAGE",
    ),
    "install.ps1": ("$UvVersion = '", "Get-FileHash", "ssw prefetch", "STEPSWITCH_PACKAGE"),
}
# Writes to a shell profile or to the user's environment variables.
PROFILE_EDIT_RE = re.compile(
    r">>\s*\S*\.(profile|bashrc|bash_profile|zshrc)\b"
    r"|SetEnvironmentVariable|(Add|Set)-Content\s+(-Path\s+)?\$PROFILE",
    re.I,
)


@check(10, "README: what it is, the quickstart, the demo, links, and the generated claims")
def _p10_readme() -> tuple[bool, str]:
    text = read("README.md")
    missing = [step for step in QUICKSTART if step not in text]
    links = [
        name
        for name in ("CONTRIBUTING.md", "SECURITY.md", "LICENSE", "docs/assets/demo.gif")
        if name not in text
    ]
    return combine(
        (bool(text), "README.md missing"),
        (not missing, "the quickstart lacks: " + ", ".join(missing)),
        (not links, "README.md does not link: " + ", ".join(links)),
        ("<!-- claims:start -->" in text, "README.md has no generated claims section"),
        ("tasks cannot run yet" not in text, "README.md still says tasks cannot run"),
    )


# A built page that would load something from the network: a remote script, stylesheet,
# image or frame, or a CSS url()/@import of a remote file.
REMOTE_ASSET_RE = re.compile(
    r"""<(?:script|link|img|source|iframe)\b[^>]*\b(?:src|href)\s*=\s*["'](?:https?:)?//"""
    r"""|url\(\s*["']?(?:https?:)?//|@import\s+(?:url\()?["']?(?:https?:)?//""",
    re.I,
)


def remote_assets(out: Path) -> list[str]:
    """Built files of the docs site that reference a remote asset."""
    return sorted(
        path.relative_to(out).as_posix()
        for path in out.rglob("*")
        if path.suffix in {".html", ".css"}
        and REMOTE_ASSET_RE.search(path.read_text(encoding="utf-8", errors="replace"))
    )


@check(10, "docs site: an ADR picks the tool, and the site builds offline with warnings as errors")
def _p10_docs_site() -> tuple[bool, str]:
    decisions = ROOT / "docs" / "decisions"
    adr = any("docs" in p.name for p in decisions.glob("003[2-9]-*.md"))
    if not adr:
        return False, "no ADR (0032 or later) chooses the docs tool"
    site = ROOT / "docs" / "site"
    if not (site / "conf.py").is_file() or not (site / "index.md").is_file():
        return False, "docs/site/conf.py or docs/site/index.md missing"
    if not has_module("sphinx"):
        return False, "Sphinx is not installed: `uv sync --group docs`"
    out = ROOT / ".stepswitch" / "docs-check"
    shutil.rmtree(out, ignore_errors=True)
    sphinx = [sys.executable, "-m", "sphinx", "-W", "--keep-going", "-q", "-b", "html"]
    code, stdout, stderr = run([*sphinx, str(site), str(out)], timeout=900)
    if code != 0:
        return False, last_line(stderr) or last_line(stdout)
    remote = remote_assets(out)
    if remote:
        return False, "remote assets in: " + ", ".join(remote[:5])
    return True, "built, no remote assets"


@check(10, "demo: a VHS tape and the GIF it records, shown in the README")
def _p10_demo() -> tuple[bool, str]:
    tape = read("docs/demo/demo.tape")
    gif = ROOT / "docs" / "assets" / "demo.gif"
    header = gif.read_bytes()[:6] if gif.is_file() else b""
    return combine(
        (bool(tape), "docs/demo/demo.tape missing"),
        ("Output docs/assets/demo.gif" in tape, "the tape does not write docs/assets/demo.gif"),
        (header in (b"GIF87a", b"GIF89a"), "docs/assets/demo.gif is missing or not a GIF"),
        (gif.is_file() and gif.stat().st_size < 8_000_000, "the GIF is larger than 8 MB"),
    )


@check(10, "examples: configurations for common setups, each validated by a test")
def _p10_examples() -> tuple[bool, str]:
    configs = sorted((ROOT / "examples").glob("*/stepswitch.toml"))
    readme = read("examples/README.md")
    unlisted = [p.parent.name for p in configs if p.parent.name not in readme]
    problems = combine(
        (len(configs) >= 3, f"examples/*/stepswitch.toml: {len(configs)} found, 3 needed"),
        (bool(readme), "examples/README.md missing"),
        (not unlisted, "examples/README.md does not list: " + ", ".join(unlisted)),
    )
    if not problems[0]:
        return problems
    return untested_or_run(("examples",), "tests/unit/test_examples.py")


@check(10, "community files: MIT license, contributing guide, security policy, code of conduct")
def _p10_community() -> tuple[bool, str]:
    missing = [name for name in COMMUNITY_FILES if not (ROOT / name).is_file()]
    project = tomllib.loads(read("pyproject.toml")).get("project", {})
    forms = sorted((ROOT / ".github" / "ISSUE_TEMPLATE").glob("*.yml"))
    bad_forms = []
    for form in forms:
        data = load_yaml(str(form.relative_to(ROOT))) or {}
        if form.name != "config.yml" and not set(data) >= ISSUE_FORM_KEYS:
            bad_forms.append(form.name)
    return combine(
        (not missing, "missing: " + ", ".join(missing)),
        (project.get("license") == "MIT", "pyproject.toml does not declare the MIT license"),
        (read("LICENSE").startswith("MIT License"), "LICENSE is not the MIT license text"),
        (
            "Contributor Covenant 3.0" in read("CODE_OF_CONDUCT.md"),
            "CODE_OF_CONDUCT.md is not the Contributor Covenant 3.0",
        ),
        ("[NOTE:" not in read("CODE_OF_CONDUCT.md"), "CODE_OF_CONDUCT.md keeps a placeholder"),
        (len([f for f in forms if f.name != "config.yml"]) >= 2, "fewer than two issue forms"),
        (not bad_forms, "issue forms lack name, description or body: " + ", ".join(bad_forms)),
    )


@check(10, "release workflow: tagged builds, trusted publishing, a changelog, pinned actions")
def _p10_release() -> tuple[bool, str]:
    workflow = read(".github/workflows/release.yml")
    if not workflow:
        return False, ".github/workflows/release.yml missing"
    unpinned = [
        match.group(1)
        for line in workflow.splitlines()
        if (match := ANY_ACTION_RE.match(line)) and not PINNED_ACTION_RE.match(line)
    ]
    return combine(
        ("tags:" in workflow and "v*" in workflow, "the workflow does not run on version tags"),
        ("id-token: write" in workflow, "no id-token permission for trusted publishing"),
        ("--no-sources" in workflow, "the build does not use `uv build --no-sources`"),
        ("npm run build" in workflow, "the dashboard is not built into the release"),
        ("git-cliff" in workflow, "no changelog step (git-cliff)"),
        ('GH_TELEMETRY: "false"' in workflow, "steps that call gh do not set GH_TELEMETRY"),
        (not unpinned, "actions not pinned to a commit: " + ", ".join(unpinned)),
        ((ROOT / "cliff.toml").is_file(), "cliff.toml missing"),
    )


@check(10, "installers: install.sh and install.ps1 pin uv, leave shell profiles alone, prefetch")
def _p10_installers() -> tuple[bool, str]:
    problems = []
    for name, needles in INSTALLER_NEEDLES.items():
        text = read(name)
        if not text:
            problems.append(f"{name} missing")
            continue
        problems += [f"{name} lacks {needle}" for needle in needles if needle not in text]
        if PROFILE_EDIT_RE.search(text):
            problems.append(f"{name} writes to a shell profile or the user environment")
    if problems:
        return False, "; ".join(problems)
    return untested_or_run(("installers",), "tests/unit/test_installers.py")


# --------------------------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------------------------


# --------------------------------------------------------------------------------------------
# Phase 11: frontier comparison (ADR 0034)
# --------------------------------------------------------------------------------------------


@check(11, "decision and research: ADR 0034 registers the comparison claims before the runs")
def _p11_decision() -> tuple[bool, str]:
    adr = read("docs/decisions/0034-frontier-comparison.md")
    research = section(read("docs/RESEARCH.md"), "Verified during Phase 11")
    missing = [cid for cid in ("C-021", "C-022", "C-023") if cid not in adr]
    return combine(
        (bool(adr), "docs/decisions/0034-frontier-comparison.md missing"),
        (not missing, "ADR 0034 does not register: " + ", ".join(missing)),
        ("--safe-mode" in research, "RESEARCH.md has no Phase 11 notes on native runs"),
    )


@check(11, "contestants: `harness:<name>` through the proxy, `native:<harness>:<model>` alone")
def _p11_contestants() -> tuple[bool, str]:
    return combine(
        help_check(["bench", "run"], ["harness:<name>", "native:<harness>:<model>"]),
        untested_or_run(("bench_frontier",), "tests/unit/test_bench_frontier.py"),
    )


@check(11, "native runs: safe mode, project settings only, no session files, no credentials")
def _p11_native_isolation() -> tuple[bool, str]:
    return untested_or_run(("claude_code_native",), "tests/unit/test_claude_code_native.py")


@check(11, "accounting: tokens per model and hosted tokens in every row, both in the leaderboard")
def _p11_accounting() -> tuple[bool, str]:
    return untested_or_run(
        ("bench_frontier", "bench_report"),
        "tests/unit/test_bench_frontier.py",
        "tests/unit/test_bench_report.py",
    )


@check(11, "progress: PROGRESS.md records the comparison runs and their usage")
def _p11_progress() -> tuple[bool, str]:
    text = section(read("docs/PROGRESS.md"), "Phase 11: Frontier comparison")
    return combine(
        (bool(text), "docs/PROGRESS.md has no `Phase 11: Frontier comparison` section"),
        ("pilot" in text.lower(), "PROGRESS.md Phase 11 does not record the pilot's usage"),
    )


# --------------------------------------------------------------------------------------------
# Phase 12: launch readiness (ADR 0037)
# --------------------------------------------------------------------------------------------

DEV_SUITE = "bench/suites/dev.yaml"


@check(12, "decision and research: ADR 0037 and ADR 0038, and the Ollama context finding")
def _p12_decision() -> tuple[bool, str]:
    adr = read("docs/decisions/0037-launch-readiness.md")
    edits = read("docs/decisions/0038-edit-shapes.md")
    research = section(read("docs/RESEARCH.md"), "Verified during Phase 12")
    return combine(
        (bool(adr), "docs/decisions/0037-launch-readiness.md missing"),
        ("C-024" in adr and "confirmatory" in adr, "ADR 0037 registers no confirmatory claim"),
        (bool(edits), "docs/decisions/0038-edit-shapes.md missing"),
        ("num_ctx" in research, "RESEARCH.md has no Phase 12 notes on the Ollama context"),
        ("Begin Patch" in research, "RESEARCH.md has no Phase 12 notes on the patch format"),
    )


@check(12, "integrity: incomplete replies fail, empty reviews approve nothing, canary, num_ctx")
def _p12_integrity() -> tuple[bool, str]:
    return untested_or_run(("launch_integrity",), "tests/unit/test_launch_integrity.py")


@check(12, "edits: old and new text, begin-patch patches and empty defaults are accepted")
def _p12_edit_shapes() -> tuple[bool, str]:
    return untested_or_run(("edit_shapes",), "tests/unit/test_edit_shapes.py")


@check(12, "effort: plan and work effort follow the task's difficulty, fixed within a task")
def _p12_effort() -> tuple[bool, str]:
    return untested_or_run(("task_effort",), "tests/unit/test_task_effort.py")


@check(12, "dev suite: tuning tasks on repositories no mini suite uses")
def _p12_dev_suite() -> tuple[bool, str]:
    yaml = YAML(typ="safe", pure=True)
    dev = yaml.load(read(DEV_SUITE) or "{}") or {}
    dev_repos = {task.get("repo") for task in dev.get("tasks", [])}
    shared: set[str] = set()
    for name in ("mini-general", "mini-science"):
        suite = yaml.load(read(f"bench/suites/{name}.yaml") or "{}") or {}
        shared |= dev_repos & {task.get("repo") for task in suite.get("tasks", [])}
    problems = combine(
        (bool(dev_repos), f"{DEV_SUITE} has no tasks"),
        (
            not shared,
            "the dev suite shares repositories with a mini suite: "
            + ", ".join(sorted(str(repo) for repo in shared)),
        ),
    )
    if not problems[0]:
        return problems
    return pytest_run("tests/unit/test_bench_suites.py")


@check(12, "front page: the README's results table is generated from passing claims")
def _p12_readme() -> tuple[bool, str]:
    readme = read("README.md")
    if "<!-- results:start -->" not in readme:
        return False, "README.md has no generated results table (<!-- results:start -->)"
    code, out, err = ssw("prove", "--render-readme", "--check")
    return code == 0, (out + err).strip().splitlines()[-1] if (out + err).strip() else "ok"


@check(12, "progress: PROGRESS.md records the confirmatory runs")
def _p12_progress() -> tuple[bool, str]:
    text = section(read("docs/PROGRESS.md"), "Phase 12: Launch readiness")
    return combine(
        (bool(text), "docs/PROGRESS.md has no `Phase 12: Launch readiness` section"),
        ("confirmatory" in text.lower(), "PROGRESS.md Phase 12 records no confirmatory run"),
    )


def run_phases(phases: list[int]) -> list[Result]:
    results = []
    for phase in phases:
        for name, fn in CHECKS[phase]:
            try:
                ok, detail = fn()
            except Exception as exc:  # noqa: BLE001 - a crashing check is a failing check
                ok, detail = False, f"check crashed: {type(exc).__name__}: {exc}"
            results.append(Result(phase, name, ok, detail))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("phase", nargs="?", type=int, help="run the checks for this phase")
    parser.add_argument("--upto", type=int, metavar="N", help="run the checks for phases 0..N")
    parser.add_argument("--json", action="store_true", help="print results as JSON")
    args = parser.parse_args(argv)
    if (args.phase is None) == (args.upto is None):
        parser.error("give exactly one of N or --upto N")
    target = args.phase if args.phase is not None else args.upto
    if not 0 <= target <= MAX_PHASE:
        parser.error(f"phases 0..{MAX_PHASE} are defined")
    phases = list(range(target + 1)) if args.upto is not None else [target]
    results = run_phases(phases)
    failed = [r for r in results if not r.ok]

    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        for phase in phases:
            print(f"Phase {phase}: {PHASE_TITLES[phase]}")
            for r in (r for r in results if r.phase == phase):
                print(f"  {'PASS' if r.ok else 'FAIL'}  {r.name}")
                if not r.ok or r.detail != "ok":
                    print(f"        {r.detail}")
        bad_phases = sorted({r.phase for r in failed})
        print(
            f"\n{len(results) - len(failed)}/{len(results)} checks passed"
            + (f"; failing phases: {', '.join(map(str, bad_phases))}" if failed else "")
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
