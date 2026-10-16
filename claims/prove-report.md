# Prove report

- Mode: quick
- Created: 2026-10-16T13:26:18+00:00
- Source digest: `0d3d27fe9fdf9f6de8882186d6648a773601d4c5f24b452926666119baa20059`
- Platform: windows amd64, Python 3.12.15

| Claim | Status | Tests |
|---|---|---|
| C-001 | passing | test_toy_task_runs_in_cpu_only_8gb_container: passed |
| C-002 | passing | test_single_task_switches_endpoints_with_logged_reasons: passed |
| C-004 | passing | test_conformance_against_mock_servers: passed, test_round_trip_preserves_messages_and_tool_calls: passed |
| C-005 | passing | test_model_without_native_tools_completes_task_via_fallback: passed, test_probe_suite_passes_on_ci_model_set: passed, test_compatibility_matrix_generated_from_probe_results: passed |
| C-007 | passing | test_default_flow_opens_no_outbound_connections: passed |
| C-008 | passing | test_mutations_above_tolerance_caught_below_not_flagged: passed |
| C-009 | passing | test_protected_change_fails_without_allow_flag: passed, test_protected_change_passes_with_allow_flag: passed |
| C-016 | pending | test_prove_quick_passed_on_linux_macos_windows: skipped |
| C-018 | passing | test_every_readme_number_has_evidence_and_command: passed |
| C-019 | passing | test_ledger_matches_server_usage_within_one_percent: passed |
| C-020 | passing | test_tiny_budgets_stop_with_status_and_bounded_overrun: passed |
