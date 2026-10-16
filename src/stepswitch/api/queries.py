"""Read models for the task API (docs/API.md): state-database rows and project files, shaped as
the dashboard reads them.

Every function takes what it reads (a `StateDB`, the project root, the configuration) and
returns plain JSON-ready data. Nothing here starts work or opens a network connection, except
`endpoints(..., check=True)`, which asks each configured endpoint for a cheap read-only page
through the egress layer. Keys, tokens and server log paths are never returned, and remote
endpoint URLs are left out (ADR 0023).
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from stepswitch.core.task import FINAL_STATUSES
from stepswitch.state import StateDB

TOKEN_FIELDS = ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens")
LEDGER_FIELDS = (*TOKEN_FIELDS, "wall_seconds", "gpu_seconds")
LEDGER_COLUMNS = (
    "step",
    "ts",
    "step_type",
    "endpoint",
    "model",
    *LEDGER_FIELDS,
    "usage_source",
)
GROUPS = ("model", "task", "day")
TASK_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
RESULTS_DIR = Path("bench") / "results"
CHART_FILE = "pass-rate.svg"


def _json(text: str | None) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _placeholders(items: Sequence[Any]) -> str:
    return ", ".join("?" for _ in items)


# --------------------------------------------------------------------------------------------
# Tasks
# --------------------------------------------------------------------------------------------


def _task_aggregates(db: StateDB, ids: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Ledger sums, decision counts, escalations and cancel requests per task."""
    out: dict[str, dict[str, Any]] = {
        task_id: {
            "tokens": dict.fromkeys(("input", "output", "cached_input", "reasoning"), 0),
            "model_seconds": 0.0,
            "gpu_seconds": 0.0,
            "steps": 0,
            "switches": 0,
            "escalations": 0,
            "cancel_requested": False,
        }
        for task_id in ids
    }
    if not ids:
        return out
    marks = _placeholders(ids)
    for row in db.query(
        "SELECT task_id, SUM(input_tokens) AS input, SUM(output_tokens) AS output,"
        " SUM(cached_input_tokens) AS cached, SUM(reasoning_tokens) AS reasoning,"
        " SUM(wall_seconds) AS wall, SUM(gpu_seconds) AS gpu"
        f" FROM ledger WHERE task_id IN ({marks}) GROUP BY task_id",
        tuple(ids),
    ):
        entry = out[row["task_id"]]
        entry["tokens"] = {
            "input": int(row["input"] or 0),
            "output": int(row["output"] or 0),
            "cached_input": int(row["cached"] or 0),
            "reasoning": int(row["reasoning"] or 0),
        }
        entry["model_seconds"] = float(row["wall"] or 0.0)
        entry["gpu_seconds"] = round(float(row["gpu"] or 0.0), 3)
    for row in db.query(
        "SELECT task_id, COUNT(*) AS steps, SUM(switched) AS switches"
        f" FROM decisions WHERE task_id IN ({marks}) GROUP BY task_id",
        tuple(ids),
    ):
        out[row["task_id"]]["steps"] = int(row["steps"] or 0)
        out[row["task_id"]]["switches"] = int(row["switches"] or 0)
    for row in db.query(
        "SELECT task_id, COUNT(*) AS n FROM events"
        f" WHERE type = 'escalation' AND task_id IN ({marks}) GROUP BY task_id",
        tuple(ids),
    ):
        out[row["task_id"]]["escalations"] = int(row["n"] or 0)
    for row in db.query(
        f"SELECT task_id FROM cancellations WHERE task_id IN ({marks})", tuple(ids)
    ):
        out[row["task_id"]]["cancel_requested"] = True
    return out


def _summary(row: Mapping[str, Any], extra: Mapping[str, Any], now: float) -> dict[str, Any]:
    spec = _json(row.get("spec")) or {}
    result = _json(row.get("result")) or {}
    ended = row["status"] in FINAL_STATUSES
    if ended and isinstance(result.get("wall_seconds"), int | float):
        wall = float(result["wall_seconds"])
    else:
        wall = (float(row["updated"]) if ended else now) - float(row["created"])
    return {
        "id": row["id"],
        "created": row["created"],
        "updated": row["updated"],
        "status": row["status"],
        "stop_reason": row.get("stop_reason"),
        "track": row["track"],
        "instruction": row["instruction"],
        "worker": spec.get("worker"),
        "steps": int(result.get("steps", extra["steps"])) if ended else extra["steps"],
        "escalations": int(result.get("escalations", extra["escalations"]))
        if ended
        else extra["escalations"],
        "switches": int(result.get("switches", extra["switches"])) if ended else extra["switches"],
        "tokens": extra["tokens"],
        "wall_seconds": round(max(0.0, wall), 3),
        "gpu_seconds": extra["gpu_seconds"],
        "summary": str(result.get("summary") or ""),
        "cancel_requested": extra["cancel_requested"],
    }


def summaries(db: StateDB, rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    extras = _task_aggregates(db, [row["id"] for row in rows])
    now = time.time()
    return [_summary(row, extras[row["id"]], now) for row in rows]


def list_tasks(db: StateDB, *, limit: int = 50, status: str | None = None) -> list[dict[str, Any]]:
    """Newest first, optionally of one status."""
    if status:
        rows = db.query(
            "SELECT * FROM tasks WHERE status = ? ORDER BY created DESC LIMIT ?", (status, limit)
        )
    else:
        rows = db.query("SELECT * FROM tasks ORDER BY created DESC LIMIT ?", (limit,))
    return summaries(db, rows)


def task_row(db: StateDB, task_id: str) -> dict[str, Any] | None:
    rows = db.query("SELECT * FROM tasks WHERE id = ?", (task_id,))
    return rows[0] if rows else None


def decision(row: Mapping[str, Any]) -> dict[str, Any]:
    data = _json(row.get("data")) or {}
    return {
        **data,
        "task_id": row["task_id"],
        "step": row["step"],
        "ts": row["ts"],
        "step_type": row["step_type"],
        "chosen": row["chosen"],
        "switched": bool(row["switched"]),
        "reason": row["reason"],
    }


def ledger_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {column: row[column] for column in LEDGER_COLUMNS}


def task_detail(db: StateDB, root: Path, task_id: str) -> dict[str, Any] | None:
    row = task_row(db, task_id)
    if row is None:
        return None
    detail = summaries(db, [row])[0]
    result = _json(row.get("result"))
    if isinstance(result, dict):
        diff = run_dir(root, task_id) / "diff.patch"
        result.setdefault("diff", diff.read_text(encoding="utf-8") if diff.is_file() else "")
    detail["spec"] = _json(row.get("spec")) or {}
    detail["result"] = result if isinstance(result, dict) else None
    detail["decisions"] = [
        decision(d)
        for d in db.query("SELECT * FROM decisions WHERE task_id = ? ORDER BY step, id", (task_id,))
    ]
    detail["ledger"] = [
        ledger_row(r)
        for r in db.query("SELECT * FROM ledger WHERE task_id = ? ORDER BY id", (task_id,))
    ]
    count = db.query("SELECT COUNT(*) AS n FROM events WHERE task_id = ?", (task_id,))
    detail["events"] = int(count[0]["n"]) if count else 0
    return detail


def task_events(db: StateDB, task_id: str, *, after: int = 0) -> list[dict[str, Any]]:
    return [
        {
            "seq": r["seq"],
            "ts": r["ts"],
            "type": r["type"],
            "step": r["step"],
            "data": _json(r["data"]) or {},
        }
        for r in db.query(
            "SELECT seq, ts, type, step, data FROM events WHERE task_id = ? AND seq > ?"
            " ORDER BY seq",
            (task_id, after),
        )
    ]


def trajectories(db: StateDB, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = db.query(
        "SELECT t.id, t.status, t.instruction, t.created,"
        " (SELECT COUNT(*) FROM events e WHERE e.task_id = t.id) AS events"
        " FROM tasks t ORDER BY t.created DESC LIMIT ?",
        (limit,),
    )
    return [dict(row) for row in rows]


def run_dir(root: Path, task_id: str) -> Path:
    from stepswitch.trajectory.record import runs_dir

    return runs_dir(root) / task_id


def run_file(root: Path, task_id: str, relative: str) -> Path | None:
    """A file inside the task's run directory, or None for anything outside it."""
    from stepswitch.trajectory.record import runs_dir

    if not TASK_ID_RE.fullmatch(task_id):
        return None
    base = run_dir(root, task_id).resolve()
    if base.parent != runs_dir(root).resolve():
        return None
    parts = PurePosixPath(relative).parts
    if not parts or "\\" in relative or ":" in relative or any(p in ("..", ".") for p in parts):
        return None
    target = base.joinpath(*parts).resolve()
    if not target.is_relative_to(base) or not target.is_file():
        return None
    return target


# --------------------------------------------------------------------------------------------
# Router, agents, ledger
# --------------------------------------------------------------------------------------------


def router(db: StateDB, *, task: str | None = None, limit: int = 200) -> dict[str, Any]:
    if task:
        rows = db.query(
            "SELECT * FROM decisions WHERE task_id = ? ORDER BY id DESC LIMIT ?", (task, limit)
        )
    else:
        rows = db.query("SELECT * FROM decisions ORDER BY id DESC LIMIT ?", (limit,))
    decisions = [decision(row) for row in rows]
    ids = sorted({d["task_id"] for d in decisions} | ({task} if task else set()))
    features: dict[str, Any] = {}
    if ids:
        for row in db.query(
            "SELECT task_id, data FROM events"
            f" WHERE type = 'classify' AND task_id IN ({_placeholders(ids)}) ORDER BY id",
            tuple(ids),
        ):
            data = _json(row["data"]) or {}
            features[row["task_id"]] = data.get("features") or {}
    return {"decisions": decisions, "features": features}


def agents(db: StateDB, contexts: Mapping[str, int | None]) -> list[dict[str, Any]]:
    """The latest model call of each task that has not ended (`contexts`: worker id -> size)."""
    final = tuple(sorted(FINAL_STATUSES))
    rows = db.query(
        f"SELECT id, status, created FROM tasks WHERE status NOT IN ({_placeholders(final)})"
        " ORDER BY created DESC",
        final,
    )
    out = []
    for task in rows:
        last = db.query(
            "SELECT * FROM ledger WHERE task_id = ? ORDER BY id DESC LIMIT 1", (task["id"],)
        )
        entry: dict[str, Any] = {
            "task_id": task["id"],
            "status": task["status"],
            "worker": None,
            "step": None,
            "step_type": None,
            "tokens_per_s": None,
            "context_used": None,
            "context": None,
            "cache_hit": None,
            "since": task["created"],
        }
        if last:
            call = last[0]
            worker = f"{call['endpoint']}/{call['model']}"
            wall = float(call["wall_seconds"] or 0.0)
            used = int(call["input_tokens"] or 0)
            entry.update(
                worker=worker,
                step=call["step"],
                step_type=call["step_type"],
                tokens_per_s=round(call["output_tokens"] / wall, 2) if wall > 0 else None,
                context_used=used,
                context=contexts.get(worker),
                cache_hit=round(call["cached_input_tokens"] / used, 4) if used else None,
            )
        else:
            chosen = db.query(
                "SELECT chosen, step, step_type FROM decisions WHERE task_id = ?"
                " ORDER BY id DESC LIMIT 1",
                (task["id"],),
            )
            if chosen:
                entry.update(
                    worker=chosen[0]["chosen"],
                    step=chosen[0]["step"],
                    step_type=chosen[0]["step_type"],
                    context=contexts.get(str(chosen[0]["chosen"])),
                )
        out.append(entry)
    return out


def _empty_totals() -> dict[str, Any]:
    return {
        **dict.fromkeys(TOKEN_FIELDS, 0),
        "wall_seconds": 0.0,
        "gpu_seconds": 0.0,
        "steps": 0,
    }


def _add(totals: dict[str, Any], row: Mapping[str, Any]) -> None:
    for name in TOKEN_FIELDS:
        totals[name] += int(row[name] or 0)
    totals["wall_seconds"] += float(row["wall_seconds"] or 0.0)
    totals["gpu_seconds"] += float(row["gpu_seconds"] or 0.0)
    totals["steps"] += 1


def _rounded(totals: dict[str, Any]) -> dict[str, Any]:
    return {
        **totals,
        "wall_seconds": round(totals["wall_seconds"], 3),
        "gpu_seconds": round(totals["gpu_seconds"], 3),
    }


def ledger(
    db: StateDB,
    *,
    group: str = "model",
    since: float | None = None,
    prices: tuple[float, float] | None = None,
    local_endpoints: Iterable[str] = (),
) -> dict[str, Any]:
    """Totals and groups of the ledger; with reference prices (per million tokens), the cost
    the local endpoints' work would have had at those prices."""
    if group not in GROUPS:
        raise ValueError(f"group must be one of {', '.join(GROUPS)}")
    rows = db.query(
        "SELECT * FROM ledger WHERE ts >= ? ORDER BY id", (since if since is not None else 0.0,)
    )
    totals = _empty_totals()
    groups: dict[str, dict[str, Any]] = {}
    local = set(local_endpoints)
    avoided = 0.0
    for row in rows:
        if group == "model":
            key = f"{row['endpoint']}/{row['model']}"
        elif group == "task":
            key = str(row["task_id"])
        else:
            key = datetime.fromtimestamp(float(row["ts"])).date().isoformat()
        _add(totals, row)
        _add(groups.setdefault(key, _empty_totals()), row)
        if prices is not None and row["endpoint"] in local:
            avoided += (row["input_tokens"] * prices[0] + row["output_tokens"] * prices[1]) / 1e6
    return {
        "totals": _rounded(totals),
        "groups": [{"key": key, **_rounded(value)} for key, value in sorted(groups.items())],
        "reference_cost": None
        if prices is None
        else {"price_in": prices[0], "price_out": prices[1], "avoided": round(avoided, 4)},
    }


def reference_prices(config: Any) -> tuple[float, float] | None:
    """`[ledger] reference_price_in` and `reference_price_out` (per million tokens), if set."""
    section = (getattr(config, "model_extra", None) or {}).get("ledger")
    if not isinstance(section, dict):
        return None
    price_in, price_out = section.get("reference_price_in"), section.get("reference_price_out")
    if isinstance(price_in, int | float) and isinstance(price_out, int | float):
        return float(price_in), float(price_out)
    return None


# --------------------------------------------------------------------------------------------
# Audit and science
# --------------------------------------------------------------------------------------------


def audit(
    db: StateDB, *, task: str | None = None, limit: int = 200, denied: bool = False
) -> dict[str, Any]:
    where, params = [], []
    if task:
        where.append("task_id = ?")
        params.append(task)
    if denied:
        where.append("allowed = 0")
    clause = f" WHERE {' AND '.join(where)}" if where else ""
    rows = db.query(
        f"SELECT * FROM egress_audit{clause} ORDER BY id DESC LIMIT ?", (*params, limit)
    )
    counts = db.query(
        "SELECT COUNT(*) AS requests, SUM(CASE WHEN allowed = 0 THEN 1 ELSE 0 END) AS denied,"
        f" SUM(CASE WHEN local = 0 THEN 1 ELSE 0 END) AS non_local FROM egress_audit{clause}",
        tuple(params),
    )[0]
    return {
        "rows": [
            {**row, "allowed": bool(row["allowed"]), "local": bool(row["local"])} for row in rows
        ],
        "totals": {
            "requests": int(counts["requests"] or 0),
            "denied": int(counts["denied"] or 0),
            "non_local": int(counts["non_local"] or 0),
        },
    }


def _plot_url(root: Path, task_id: str, path: str) -> str | None:
    base = run_dir(root, task_id).resolve()
    try:
        relative = Path(path).resolve().relative_to(base)
    except (ValueError, OSError):
        return None
    return f"/api/runs/{task_id}/files/{relative.as_posix()}"


def science(db: StateDB, root: Path, *, task: str | None = None) -> list[dict[str, Any]]:
    """Golden-gate reports from the tasks' gate runs, newest first."""
    if task:
        rows = db.query(
            "SELECT task_id, step, data FROM events WHERE type = 'gates' AND task_id = ?"
            " ORDER BY id DESC",
            (task,),
        )
    else:
        rows = db.query(
            "SELECT task_id, step, data FROM events WHERE type = 'gates' ORDER BY id DESC LIMIT 200"
        )
    reports = []
    for row in rows:
        data = _json(row["data"]) or {}
        verdict = data.get("verdict") or {}
        for gate in verdict.get("results") or []:
            details = gate.get("details") if isinstance(gate, dict) else None
            if gate.get("name") != "golden" or not isinstance(details, dict):
                continue
            if "cases" not in details:
                continue
            plots = [_plot_url(root, row["task_id"], str(p)) for p in details.get("plots") or []]
            reports.append(
                {
                    "task_id": row["task_id"],
                    "run": data.get("run"),
                    "step": row["step"],
                    "status": gate.get("status"),
                    "summary": gate.get("summary") or "",
                    "table": details.get("table") or "",
                    "cases": [
                        {"case": c.get("case"), "status": c.get("status"), "error": c.get("error")}
                        for c in details.get("cases") or []
                        if isinstance(c, dict)
                    ],
                    "plots": [url for url in plots if url],
                }
            )
    return reports


# --------------------------------------------------------------------------------------------
# Bench results, claims, compatibility
# --------------------------------------------------------------------------------------------


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def bench_runs(root: Path) -> list[dict[str, Any]]:
    """Result folders with a leaderboard, newest first."""
    folder = root / RESULTS_DIR
    if not folder.is_dir():
        return []
    runs = []
    for path in folder.iterdir():
        board = _read_json(path / "leaderboard.json") if path.is_dir() else None
        if not isinstance(board, dict):
            continue
        manifest = _read_json(path / "run.json")
        manifest = manifest if isinstance(manifest, dict) else {}
        runs.append(
            {
                "name": path.name,
                "suite": board.get("suite"),
                "started": manifest.get("started") or board.get("date"),
                "finished": manifest.get("finished") or board.get("date"),
                "contestants": [
                    c.get("contestant")
                    for c in board.get("contestants") or []
                    if isinstance(c, dict)
                ],
                "rows": board.get("runs"),
            }
        )
    return sorted(runs, key=lambda r: (str(r["finished"] or ""), r["name"]), reverse=True)


def leaderboard(root: Path, run: str | None = None) -> dict[str, Any]:
    runs = bench_runs(root)
    names = {r["name"] for r in runs}
    if run is not None and run not in names:
        raise KeyError(run)
    chosen = run or (runs[0]["name"] if runs else None)
    board = _read_json(root / RESULTS_DIR / chosen / "leaderboard.json") if chosen else None
    chart = root / RESULTS_DIR / chosen / CHART_FILE if chosen else None
    return {
        "runs": runs,
        "run": chosen,
        "leaderboard": board,
        "chart": f"/api/leaderboard/{chosen}/{CHART_FILE}" if chart and chart.is_file() else None,
    }


def chart_file(root: Path, run: str) -> Path | None:
    if run not in {r["name"] for r in bench_runs(root)}:
        return None
    path = root / RESULTS_DIR / run / CHART_FILE
    return path if path.is_file() else None


def claims(root: Path) -> dict[str, Any]:
    """The newest prove report, with each claim's text from claims/claims.yaml when present."""
    candidates = [root / "prove-report.json", root / "claims" / "prove-report.json"]
    reports = [(p, _read_json(p)) for p in candidates if p.is_file()]
    reports = [(p, r) for p, r in reports if isinstance(r, dict)]
    source, report = max(
        reports, key=lambda item: str(item[1].get("created") or ""), default=(None, None)
    )
    texts: dict[str, dict[str, Any]] = {}
    registry_file = root / "claims" / "claims.yaml"
    if registry_file.is_file():
        from ruamel.yaml import YAML

        data = YAML(typ="safe", pure=True).load(registry_file.read_text(encoding="utf-8")) or {}
        for claim in data.get("claims") or []:
            if isinstance(claim, dict) and claim.get("id"):
                texts[str(claim["id"])] = claim
    results = (report or {}).get("claims") or {}
    ids = sorted(set(texts) | set(results))
    rows = []
    for claim_id in ids:
        entry = texts.get(claim_id, {})
        outcome = results.get(claim_id) or {}
        rows.append(
            {
                "id": claim_id,
                "text": entry.get("text"),
                "phase": entry.get("phase"),
                "kind": entry.get("kind"),
                "status": outcome.get("status") or entry.get("status") or "unknown",
                "tests": outcome.get("tests") or {},
            }
        )
    meta = {key: value for key, value in (report or {}).items() if key != "claims"}
    return {
        "source": source.relative_to(root).as_posix() if source else None,
        "report": meta or None,
        "claims": rows,
    }


def compatibility(root: Path, db: StateDB) -> dict[str, Any]:
    from stepswitch import registry
    from stepswitch.compat import matrix, probe

    results = []
    ci_set: list[str] = []
    problems: list[str] = []
    if (root / matrix.RESULTS_DIR).is_dir():
        try:
            ci_set = matrix.load_ci_set(root).models
        except (OSError, matrix.MatrixError):
            ci_set = []
        entries = registry.load()
        loaded = []
        for path in sorted((root / matrix.RESULTS_DIR).glob("*.json")):
            try:
                loaded.append(matrix.load_result(path))
            except (OSError, ValueError) as exc:  # MatrixError and JSONDecodeError
                problems.append(f"{path.name}: {exc}")
        for result in loaded:
            outcome = matrix.verdict(result)
            entry = entries.get(result.model)
            results.append(
                {
                    "file": result.path.name,
                    "model": result.model,
                    "family": entry.family if entry is not None else None,
                    "engine": result.engine,
                    "engine_version": result.engine_version,
                    "format": result.format,
                    "date": result.date,
                    "passes": outcome.passed,
                    "failed": outcome.failed,
                    "tool_path": outcome.tool_path,
                    "tool_parser": result.tool_parser,
                    "checks": {name: result.shown(name) for name in probe.CHECKS},
                }
            )
    local = []
    for row in db.query(
        "SELECT endpoint, model, format, created, profile FROM probes p WHERE created ="
        " (SELECT MAX(created) FROM probes q WHERE q.endpoint = p.endpoint AND q.model = p.model)"
        " ORDER BY endpoint, model"
    ):
        profile = _json(row["profile"]) or {}
        local.append(
            {
                "endpoint": row["endpoint"],
                "model": row["model"],
                "format": row["format"],
                "created": row["created"],
                "capabilities": profile.get("capabilities") or {},
            }
        )
    return {
        "ci_set": ci_set,
        "checks": [{"name": n, "label": matrix.CHECK_LABELS.get(n, n)} for n in probe.CHECKS],
        "results": results,
        "local": local,
        "problems": problems,
    }


# --------------------------------------------------------------------------------------------
# Workers and endpoints
# --------------------------------------------------------------------------------------------


def workers(catalog: Any) -> dict[str, Any]:
    rows = []
    for spec in catalog.specs():
        rows.append(
            {**spec.as_dict(), "prefill_tps": spec.prefill_tps, "decode_tps": spec.decode_tps}
        )
    return {"workers": rows, "notes": list(catalog.notes)}


def latest_probes(db: StateDB, endpoint: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for row in db.query(
        "SELECT model, format, created, profile FROM probes p WHERE endpoint = ? AND created ="
        " (SELECT MAX(created) FROM probes q WHERE q.endpoint = p.endpoint AND q.model = p.model)",
        (endpoint,),
    ):
        profile = _json(row["profile"]) or {}
        out[row["model"]] = {
            "created": row["created"],
            "format": row["format"],
            "capabilities": profile.get("capabilities") or {},
        }
    return out


def endpoint_rows(config: Any, db: StateDB) -> list[dict[str, Any]]:
    rows = []
    for endpoint in config.endpoints:
        local = endpoint.is_local()
        rows.append(
            {
                "name": endpoint.name,
                "engine": endpoint.engine,
                "format": endpoint.primary_format(),
                "formats": list(endpoint.formats),
                "local": local,
                "url": endpoint.url if local else None,
                "auth": endpoint.auth,
                "models": list(endpoint.models),
                "tool_parser": endpoint.tool_parser,
                "probes": latest_probes(db, endpoint.name),
            }
        )
    return rows


def managed_rows(servers: Iterable[Any], *, checked: bool) -> list[dict[str, Any]]:
    """Managed servers, without their keys or log paths; `alive` is known only when the
    servers were `checked`."""
    return [
        {
            "model": info.model,
            "engine": info.engine,
            "base_url": info.base_url,
            "alive": True if checked else None,
            "started": info.started,
            "last_used": info.last_used,
        }
        for info in servers
    ]


def installed_rows(
    entries: Iterable[Any], models_dir: Path, running: set[str], states: Mapping[str, str]
) -> list[dict[str, Any]]:
    rows = []
    for entry in sorted(entries, key=lambda e: e.id):
        if not entry.installed(models_dir):
            continue
        state = states.get(entry.id)
        rows.append(
            {
                "id": entry.id,
                "name": entry.name,
                "ram_gb": entry.ram_gb,
                "roles": list(entry.roles),
                "running": entry.id in running,
                "state": "running" if entry.id in running else (state or "stopped"),
            }
        )
    return rows
