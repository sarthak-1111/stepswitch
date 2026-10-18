"""C-010: handoff briefs validate against the HANDOFF v0.1 schema and stay under 1,500 tokens.

Proof (SPEC §18.5): schema validation and a tokenizer count on every handoff produced during
the bench suites. Scope: every handoff produced during the bench suites, counted with the
receiving model's tokenizer.

Every optimized bench run (`run.json` has `"optimize": true`) writes one record per brief it
produced to `handoffs.jsonl`: the brief, the Markdown that was sent, the token count, which
tokenizer counted it and whether that count is exact (SPEC §9). This test checks, for every
record of both mini suites:

* the brief validates against `spec/handoff-v0.1.schema.json` (with jsonschema, offline);
* the Markdown is the brief rendered by `render_markdown`, so the count is of what was sent;
* the count is exact and at most the threshold, and the brief's provenance names the same
  tokenizer;
* where an exact tokenizer for the receiving model is available on this machine, a fresh count
  equals the recorded one;
* the number of records equals the `handoffs` counts in the runs' result rows, so no brief is
  missing from the records.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from stepswitch.context import handoff as handoff_module
from stepswitch.context import tokens as tokens_module
from tests.claims import evidence
from tests.claims.bench_runs import ROOT, runs

pytestmark = [pytest.mark.claim("C-010")]

SCHEMA = ROOT / "spec" / "handoff-v0.1.schema.json"
SUITES = ("mini-general", "mini-science")


def test_bench_handoffs_validate_and_fit_token_budget() -> None:
    limits = evidence.thresholds("C-010")
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    optimized = [run for run in runs() if run.optimize and run.suite in SUITES]
    assert optimized, "no optimized bench runs of the mini suites under bench/results"

    records: list[tuple[str, dict[str, Any]]] = []
    missing_counts: list[str] = []
    for run in optimized:
        expected = sum(int(row.get("handoffs") or 0) for row in run.rows)
        if expected != len(run.handoffs):
            missing_counts.append(f"{run.name}: rows say {expected}, records {len(run.handoffs)}")
        records += [(run.name, record) for record in run.handoffs]

    problems: list[str] = []
    valid = 0
    recounted = 0
    largest = 0
    tokenizers: dict[str, int] = {}
    reasons: dict[str, int] = {}
    per_suite: dict[str, int] = {}
    for name, record in records:
        brief = record.get("brief")
        label = f"{name} {record.get('task')} seed {record.get('seed')} step {record.get('step')}"
        errors = list(validator.iter_errors(brief))
        if errors:
            problems.append(f"{label}: schema: {errors[0].message}")
        else:
            valid += 1
        if record.get("markdown") != handoff_module.render_markdown(brief or {}):
            problems.append(f"{label}: markdown is not the rendered brief")
        count = int(record.get("tokens", -1))
        largest = max(largest, count)
        if count < 0 or count > limits["max_tokens"]:
            problems.append(f"{label}: {count} tokens")
        if record.get("exact") is not True:
            problems.append(f"{label}: count is not exact ({record.get('tokenizer')})")
        provenance = (brief or {}).get("provenance", {})
        if provenance.get("tokenizer") != record.get("tokenizer"):
            problems.append(f"{label}: provenance names another tokenizer")
        counter = tokens_module.resolve_local(record.get("receiver") or {})
        if counter is not None and counter.exact:
            recounted += 1
            fresh = counter.count(record.get("markdown") or "")
            if fresh != count:
                problems.append(f"{label}: recount {fresh} != recorded {count}")
        tokenizer = str(record.get("tokenizer"))
        tokenizers[tokenizer] = tokenizers.get(tokenizer, 0) + 1
        reasons[str(record.get("reason"))] = reasons.get(str(record.get("reason")), 0) + 1
        suite = str(record.get("suite"))
        per_suite[suite] = per_suite.get(suite, 0) + 1

    total = len(records)
    evidence.write(
        "C-010",
        "handoffs",
        {
            "runs": [run.name for run in optimized],
            "handoffs": total,
            "per_suite": per_suite,
            "reasons": reasons,
            "schema_valid_fraction": round(valid / total, 4) if total else None,
            "max_tokens": largest,
            "tokenizers": tokenizers,
            "recounted": recounted,
            "problems": problems[:20],
            "missing_records": missing_counts,
        },
    )
    assert total >= limits["min_handoffs"], f"only {total} handoffs in the bench runs"
    assert all(per_suite.get(suite) for suite in SUITES), per_suite
    assert not missing_counts, missing_counts
    assert valid / total >= limits["schema_valid"], problems[:10]
    assert not problems, problems[:10]
