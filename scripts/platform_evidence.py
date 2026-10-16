"""Combine per-platform prove reports into claims/evidence/C-016.json (claim C-016, ADR 0011).

Usage (the CI aggregation job; also works on reports collected by hand):

    uv run python scripts/platform_evidence.py REPORT_DIR [REPORT_DIR ...] [--run-url URL]

Each REPORT_DIR holds one platform's `prove-report.json` (anywhere below it). A platform whose
report is missing is recorded as a failed run, never left out.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from stepswitch.prove.digest import source_digest  # noqa: E402
from stepswitch.prove.runner import platform_info  # noqa: E402

CLAIM = "C-016"


def run_from(report_dir: Path) -> dict[str, Any]:
    found = sorted(report_dir.rglob("prove-report.json"))
    if not found:
        return {"report_dir": report_dir.name, "report_found": False}
    report = json.loads(found[0].read_text(encoding="utf-8"))
    info = report.get("platform", {})
    summary = report.get("summary", {})
    return {
        "report_dir": report_dir.name,
        "report_found": True,
        "os": info.get("os"),
        "machine": info.get("machine"),
        "python": info.get("python"),
        "runner_label": info.get("runner_label"),
        "image_os": info.get("image_os"),
        "image_version": info.get("image_version"),
        "mode": report.get("mode"),
        "source_digest": report.get("source_digest"),
        "created": report.get("created"),
        "passing": summary.get("passing"),
        "failing": summary.get("failing"),
        "pending": summary.get("pending"),
        "skipped_claims": report.get("selection", {}).get("skip", []),
        "claims": {cid: entry.get("status") for cid, entry in report.get("claims", {}).items()},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("report_dirs", nargs="+", type=Path)
    parser.add_argument("--run-url", default=None, help="URL of the CI run, for the record")
    args = parser.parse_args(argv)
    from ruamel.yaml import YAML

    registry = YAML(typ="safe", pure=True).load(
        (ROOT / "claims" / "claims.yaml").read_text(encoding="utf-8")
    )
    claim = next(c for c in registry["claims"] if c["id"] == CLAIM)
    runs = [run_from(path) for path in args.report_dirs]
    now = datetime.now(UTC).isoformat(timespec="seconds")
    evidence = {
        "claim": CLAIM,
        "text": claim["text"],
        "scope": claim["scope"],
        "thresholds": claim["thresholds"],
        "command": "CI workflow `prove` (.github/workflows/prove.yml), then "
        "`uv run python scripts/platform_evidence.py <report dirs>`",
        "mode": "quick",
        "source_digest": source_digest(ROOT),
        "environment": platform_info(),
        "updated": now,
        "sections": {"platforms": {"recorded": now, "run_url": args.run_url, "runs": runs}},
    }
    out = ROOT / "claims" / "evidence" / f"{CLAIM}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    for run in runs:
        print(
            f"{run.get('os', run['report_dir'])}: report={run['report_found']} "
            f"failing={run.get('failing')} label={run.get('runner_label')}"
        )
    print(f"wrote {out.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
