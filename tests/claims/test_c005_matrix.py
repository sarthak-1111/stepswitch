"""C-005 (matrix part): the probe suite passes on the CI model set, and the compatibility matrix
is generated from the probe results.

Proof (SPEC §18.5): probe suite passes on the CI model set (at least four families);
compatibility matrix generated from results. Scope: models served by a supported engine that is
installed; the compatibility matrix lists exactly what has been verified (SPEC §4.8).

The CI model set is `probes/ci-set.yaml`: tiny models from different families, served by the
llama.cpp server on CPU. Their probe results are committed in `probes/` (`ssw probe --ci-set`
writes them; ADR 0021 says what "passes" means). In quick mode the test validates the committed
results, as ADR 0006 asks for probe-matrix claims: each must be a full probe of exactly the
registry's model file (same sha256) and pass. In full mode it first probes every model of the
set again and rewrites the results. `docs/COMPATIBILITY.md` must be exactly what
`ssw probe --render-matrix` renders from the results.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stepswitch import registry
from stepswitch.compat import matrix
from tests.claims import evidence

pytestmark = [pytest.mark.claim("C-005")]

ROOT = Path(__file__).resolve().parents[2]
COMMAND = "uv run ssw prove --full --claim C-005"


def test_probe_suite_passes_on_ci_model_set() -> None:
    limits = evidence.thresholds("C-005")
    ci_set = matrix.load_ci_set(ROOT)
    if evidence.prove_mode() == "full":
        from tests.claims.local_models import installed, llamacpp_server

        missing = [model for model in ci_set.models if installed(model) is None]
        if missing or llamacpp_server() is None:
            pytest.skip(f"pending: full mode needs the CI model set installed ({missing})")
        matrix.probe_ci_set(ROOT, echo=lambda _msg: None)
    entries = registry.load()
    results = {r.model: r for r in matrix.load_results(ROOT) if r.engine == ci_set.engine}
    rows = []
    for model in ci_set.models:
        entry = entries[model]
        result = results.get(model)
        verdict = matrix.verdict(result) if result is not None else None
        rows.append(
            {
                "model": model,
                "family": entry.family,
                "result": result.path.name if result is not None else None,
                "date": result.date if result is not None else None,
                "engine": ci_set.engine,
                "engine_version": result.engine_version if result is not None else None,
                "full_probe": result is not None and not result.quick,
                "hash_matches": result is not None
                and result.model_hash == f"sha256:{entry.sha256}",
                "passes": verdict is not None and verdict.passed,
                "tool_path": verdict.tool_path if verdict is not None else None,
                "failed_checks": verdict.failed if verdict is not None else ["no result"],
            }
        )
    valid = [r for r in rows if r["full_probe"] and r["hash_matches"] and r["passes"]]
    families = sorted({str(r["family"]) for r in valid})
    evidence.write(
        "C-005",
        "matrix",
        {
            "ci_set": ci_set.models,
            "engine": ci_set.engine,
            "models": rows,
            "families_passing": families,
            "families_passing_count": len(families),
            "min_families": limits["min_families"],
            "fallback_models": [r["model"] for r in valid if r["tool_path"] == "fallback"],
        },
        command=COMMAND,
    )
    assert len({entries[m].family for m in ci_set.models}) >= limits["min_families"], rows
    assert rows == valid, [r for r in rows if r not in valid]
    assert len(families) >= limits["min_families"], families


def test_compatibility_matrix_generated_from_probe_results() -> None:
    results = matrix.load_results(ROOT)
    text = (ROOT / matrix.MATRIX_FILE).read_text(encoding="utf-8")
    assert matrix.GENERATED_MARKER in text
    assert text == matrix.render(ROOT), "run `ssw probe --render-matrix`"
    for result in results:
        assert f"`{result.path.name}`" in text, result.path.name
    for model in matrix.load_ci_set(ROOT).models:
        assert f"| {model} |" in text, model
