# Prove report

- Mode: quick
- Created: 2026-10-20T06:11:46+00:00
- Source digest: `1a1889373b3089574c3f3bc6d4338676861782dd30108d96dfe61ec82710bba1`
- Platform: windows amd64, Python 3.12.15

| Claim | Status | Tests |
|---|---|---|
| C-001 | passing | test_toy_task_runs_in_cpu_only_8gb_container: passed |
| C-002 | passing | test_single_task_switches_endpoints_with_logged_reasons: passed |
| C-003 | passing | test_switch_iff_gain_exceeds_cost_plus_margin: passed |
| C-004 | passing | test_conformance_against_mock_servers: passed, test_round_trip_preserves_messages_and_tool_calls: passed |
| C-005 | passing | test_model_without_native_tools_completes_task_via_fallback: passed, test_probe_suite_passes_on_ci_model_set: passed, test_compatibility_matrix_generated_from_probe_results: passed |
| C-006 | passing | test_offline_flow_makes_only_local_connections: passed, test_audit_log_matches_observed_connections: passed |
| C-007 | passing | test_default_flow_opens_no_outbound_connections: passed |
| C-008 | passing | test_mutations_above_tolerance_caught_below_not_flagged: passed |
| C-009 | passing | test_protected_change_fails_without_allow_flag: passed, test_protected_change_passes_with_allow_flag: passed |
| C-010 | passing | test_bench_handoffs_validate_and_fit_token_budget: passed |
| C-011 | passing | test_optimized_uses_fewer_tokens_at_same_pass_rate: passed |
| C-012 | passing | test_best_of_n_matches_or_beats_best_single_worker: passed |
| C-013 | passing | test_ambiguous_tasks_flagged_unambiguous_not: passed |
| C-014 | passing | test_replay_reproduces_diffs_and_gate_results: passed |
| C-015 | passing | test_bandit_regret_below_random_and_fixed_policies: passed |
| C-016 | pending | test_prove_quick_passed_on_linux_macos_windows: skipped |
| C-017 | passing | test_install_to_first_task_under_five_minutes: passed |
| C-018 | passing | test_every_readme_number_has_evidence_and_command: passed |
| C-019 | passing | test_ledger_matches_server_usage_within_one_percent: passed |
| C-020 | passing | test_tiny_budgets_stop_with_status_and_bounded_overrun: passed |
| C-022 | passing | test_local_router_processes_fewer_tokens_than_claude_code: passed |
| C-023 | passing | test_claude_code_on_local_models_passes_as_many_task_runs: passed |
| C-021 | withdrawn | not proven: docs/decisions/0041-claims-withdrawn-after-phase-12.md |
| C-024 | withdrawn | not proven: docs/decisions/0041-claims-withdrawn-after-phase-12.md |
| C-025 | withdrawn | not proven: docs/decisions/0041-claims-withdrawn-after-phase-12.md |
