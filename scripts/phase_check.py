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
import subprocess
import sys
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[1]
MAX_PHASE = 4
PHASE_TITLES = {
    0: "Research and design",
    1: "Foundation and compatibility core",
    2: "Built-in agent, router v1, gates, tracks",
    3: "Bench, leaderboard, compatibility matrix",
    4: "Dashboard and task API",
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
    found = list((ROOT / "tests").rglob("test_*dashboard*.py")) + list(
        (ROOT / "dashboard").rglob("*.spec.ts")
    )
    return bool(found), "ok" if found else "no Playwright smoke test found"


@check(4, "README claims section matches `ssw prove --render-readme`")
def _p4_readme() -> tuple[bool, str]:
    code, out, err = ssw("prove", "--render-readme", "--check")
    return code == 0, "README is current" if code == 0 else (last_line(err) or last_line(out))


# --------------------------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------------------------


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
