"""`ssw proxy`: one local endpoint for external harnesses (SPEC §12 item 2, ADR 0004, ADR 0029).

It accepts every inbound API format of SPEC §4.1, routes each request with the router, calls
the chosen worker in that worker's own format and answers in the caller's format. Models switch
only at a new user turn, never inside an open tool loop. Every request is a step of a proxy
task, so harness runs appear in the ledger, the trajectories and the dashboard like built-in
agent runs.
"""
