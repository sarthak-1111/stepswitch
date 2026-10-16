"""Verification (SPEC §8, §6.2): gates, test impact analysis and science-track checks.

* `gates.run_gates` is the entry point tasks use: it runs a track's gates, cheapest first,
  and returns a verdict with a distilled report for the worker.
* `impact` selects the tests affected by changed files.
* `science` compares golden outputs within declared tolerances, guards protected values and
  records reproducibility facts; `plot` draws its overlay plots without a plotting library.
* `commands` finds the project's interpreter and tools and runs them with time limits.

Submodules are imported on demand, so importing this package stays cheap.
"""
