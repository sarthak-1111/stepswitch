"""Bench reports (SPEC §16): `leaderboard.json`, `leaderboard.md` and a pass-rate chart.

A run directory holds `results.jsonl` (one row per task, contestant and seed) and `run.json`
(the manifest: suite, command, date, hardware, version, seeds, prices). The report is computed
from those two files only, so anyone can regenerate it, and every number it publishes names
its results file, command, hardware and date.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from stepswitch.bench.metrics import (
    DEFAULT_CONFIDENCE,
    DEFAULT_RESAMPLES,
    DEFAULT_SEED,
    Summary,
    summarize,
)

SCHEMA = 1
RESULTS_FILE = "results.jsonl"
MANIFEST_FILE = "run.json"
LEADERBOARD_JSON = "leaderboard.json"
LEADERBOARD_MD = "leaderboard.md"
CHART_SVG = "pass-rate.svg"


def read_results(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open(encoding="utf-8") as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError as exc:
                raise ValueError(f"{path.name} line {number}: {exc}") from None
            if not isinstance(row, dict) or not {"contestant", "task"} <= set(row):
                raise ValueError(f"{path.name} line {number}: needs contestant and task")
            rows.append(row)
    return rows


def read_manifest(run_dir: Path) -> dict[str, Any]:
    path = run_dir / MANIFEST_FILE
    if not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def leaderboard(
    rows: Iterable[Mapping[str, Any]],
    manifest: Mapping[str, Any] | None = None,
    *,
    confidence: float = DEFAULT_CONFIDENCE,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> dict[str, Any]:
    """The leaderboard as plain data: provenance plus one entry per contestant, best first."""
    manifest = dict(manifest or {})
    prices = manifest.get("prices") or {}
    rows = list(rows)
    summaries = summarize(
        rows,
        price_in=prices.get("input_per_million"),
        price_out=prices.get("output_per_million"),
        confidence=confidence,
        resamples=resamples,
        seed=seed,
    )
    return {
        "schema": SCHEMA,
        "suite": manifest.get("suite"),
        "results_file": RESULTS_FILE,
        "command": manifest.get("command"),
        "date": manifest.get("finished") or manifest.get("started"),
        "hardware": manifest.get("hardware"),
        "version": manifest.get("version"),
        "commit": manifest.get("commit"),
        "seeds": manifest.get("seeds"),
        "tasks": len({str(r["task"]) for r in rows}),
        "runs": len(rows),
        "prices": prices or None,
        "interval": {"confidence": confidence, "resamples": resamples, "seed": seed},
        "contestants": [_entry(rank, s) for rank, s in enumerate(summaries, 1)],
    }


def _entry(rank: int, summary: Summary) -> dict[str, Any]:
    data = summary.to_dict()
    data["rank"] = rank
    return data


def _percent(value: float | None) -> str:
    if value is None or math.isnan(value):
        return "-"
    return f"{value * 100:.0f}%"


def _number(value: float | None, digits: int = 1) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    return f"{value:,.{digits}f}"


def _hardware_text(hardware: Any) -> str:
    if isinstance(hardware, Mapping):
        parts = [f"{key} {value}" for key, value in hardware.items() if value not in (None, "")]
        return ", ".join(parts) or "not recorded"
    return str(hardware) if hardware else "not recorded"


def render_markdown(board: Mapping[str, Any]) -> str:
    interval = board.get("interval") or {}
    confidence = interval.get("confidence", DEFAULT_CONFIDENCE)
    level = f"{confidence * 100:.0f}%"
    lines = [
        f"# Leaderboard: {board.get('suite') or 'bench'}",
        "",
        f"- Results: [`{board['results_file']}`]({board['results_file']}) "
        f"({board['runs']} runs, {board['tasks']} tasks, seeds {board.get('seeds') or '-'})",
        f"- Command: `{board.get('command') or 'not recorded'}`",
        f"- Hardware: {_hardware_text(board.get('hardware'))}",
        f"- Date: {board.get('date') or 'not recorded'}",
        f"- Version: {board.get('version') or '-'}"
        + (f" (commit {board['commit']})" if board.get("commit") else ""),
        f"- Pass-rate intervals: {level}, cluster bootstrap over tasks "
        f"({interval.get('resamples', DEFAULT_RESAMPLES):,} resamples, "
        f"seed {interval.get('seed', DEFAULT_SEED)}); Wilson score interval with one task.",
    ]
    prices = board.get("prices")
    if prices:
        lines.append(
            "- Reference cost: at "
            f"{prices.get('input_per_million', 0)} per million input tokens and "
            f"{prices.get('output_per_million', 0)} per million output tokens."
        )
    lines += [
        "",
        f"| Rank | Contestant | Pass rate | {level} interval | Runs | Median wall (s) "
        "| Median tokens | GPU-s | Escalations | Cache hits | Reference cost |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for entry in board["contestants"]:
        ci = entry["ci"]
        cost = entry["reference_cost"]
        lines.append(
            f"| {entry['rank']} | {entry['contestant']} | {_percent(entry['pass_rate'])} "
            f"| {_percent(ci['low'])} to {_percent(ci['high'])} | {entry['runs']} "
            f"| {_number(entry['median_wall_seconds'])} | {_number(entry['median_tokens'], 0)} "
            f"| {_number(entry['gpu_seconds'])} | {_percent(entry['escalation_rate'])} "
            f"| {_percent(entry['cache_hit_rate'])} "
            f"| {_number(cost, 4) if cost is not None else '-'} |"
        )
    lines += ["", f"![Pass rate by contestant]({CHART_SVG})", ""]
    return "\n".join(lines)


def render_svg(board: Mapping[str, Any]) -> str:
    """Horizontal bars of the pass rate with interval whiskers, one row per contestant."""
    entries: Sequence[Mapping[str, Any]] = board["contestants"]
    label_w, bar_w, row_h, top = 220, 400, 26, 34
    width = label_w + bar_w + 70
    height = top + row_h * max(1, len(entries)) + 30
    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}" font-family="sans-serif" font-size="12">',
        f"<title>Pass rate: {escape(str(board.get('suite') or 'bench'))}</title>",
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{label_w}" y="18" fill="#333">Pass rate with interval '
        f"({escape(str(board.get('results_file')))})</text>",
    ]
    for tick in range(0, 101, 25):
        x = label_w + bar_w * tick / 100
        out.append(
            f'<line x1="{x:.1f}" y1="{top - 6}" x2="{x:.1f}" y2="{height - 24}" stroke="#ddd"/>'
        )
        out.append(
            f'<text x="{x:.1f}" y="{height - 10}" text-anchor="middle" fill="#666">{tick}%</text>'
        )
    for index, entry in enumerate(entries):
        y = top + index * row_h
        rate = float(entry["pass_rate"])
        ci = entry["ci"]
        out.append(
            f'<text x="{label_w - 8}" y="{y + 15}" text-anchor="end" fill="#222">'
            f"{escape(str(entry['contestant']))}</text>"
        )
        out.append(
            f'<rect x="{label_w}" y="{y + 4}" width="{bar_w * rate:.1f}" height="{row_h - 10}" '
            'fill="#4c78a8"/>'
        )
        low, high = ci.get("low"), ci.get("high")
        if (
            isinstance(low, float | int)
            and isinstance(high, float | int)
            and not (math.isnan(low) or math.isnan(high))
        ):
            x1, x2 = label_w + bar_w * low, label_w + bar_w * high
            mid = y + row_h / 2
            out.append(
                f'<line x1="{x1:.1f}" y1="{mid:.1f}" x2="{x2:.1f}" y2="{mid:.1f}" '
                'stroke="#222" stroke-width="1.5"/>'
            )
            for x in (x1, x2):
                out.append(
                    f'<line x1="{x:.1f}" y1="{mid - 5:.1f}" x2="{x:.1f}" y2="{mid + 5:.1f}" '
                    'stroke="#222" stroke-width="1.5"/>'
                )
        out.append(
            f'<text x="{label_w + bar_w + 8}" y="{y + 15}" fill="#222">{_percent(rate)}</text>'
        )
    out.append("</svg>")
    return "\n".join(out) + "\n"


def write_report(
    run_dir: Path,
    *,
    confidence: float = DEFAULT_CONFIDENCE,
    resamples: int = DEFAULT_RESAMPLES,
    seed: int = DEFAULT_SEED,
) -> dict[str, Path]:
    """Compute the leaderboard from `run_dir`'s results and write the three report files."""
    rows = read_results(run_dir / RESULTS_FILE)
    board = leaderboard(
        rows, read_manifest(run_dir), confidence=confidence, resamples=resamples, seed=seed
    )
    paths = {
        "json": run_dir / LEADERBOARD_JSON,
        "markdown": run_dir / LEADERBOARD_MD,
        "chart": run_dir / CHART_SVG,
    }
    paths["json"].write_text(
        json.dumps(_finite(board), indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    paths["markdown"].write_text(render_markdown(board), encoding="utf-8", newline="\n")
    paths["chart"].write_text(render_svg(board), encoding="utf-8", newline="\n")
    return paths


def _finite(value: Any) -> Any:
    """NaN and infinities become null, so the JSON stays standard."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Mapping):
        return {key: _finite(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_finite(item) for item in value]
    return value
