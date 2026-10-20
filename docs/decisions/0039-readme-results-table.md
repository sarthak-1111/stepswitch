# ADR 0039: A generated results table on the README's front page

- Status: accepted
- Date: 2026-10-20
- Phase: 12

## Context

SPEC §24 puts a "leaderboard image" right after the quickstart. A reader deciding whether to
try Stepswitch wants that comparison first: pass rate, tokens, hosted tokens and time, beside
Claude Code on hosted models. The claims section already holds these numbers, but spread over
four claim lines and in prose, at the bottom of the page.

An image cannot be checked. The integrity lint (SPEC §18.3 item 3, claim C-018) can only check
numbers that are text inside `num` markers. A hand-made table would be typed by hand, which
rule 3 of CLAUDE.md forbids.

## Decision

1. The README gets a results table right after the quickstart, in place of the leaderboard
   image. `ssw prove --render-readme` writes it between `<!-- results:start -->` and
   `<!-- results:end -->`, the same way it writes the claims section, and
   `ssw prove --render-readme --check` fails when the README differs from what it would write.
2. The table is specified by a top-level `results` entry in claims/claims.yaml: its columns and
   its rows. A row has a label, a unit and a precision, and one cell per column. Each cell is
   `<claim>:<evidence key>`.
3. A cell shows its value, wrapped in a `num` marker, only while its claim is passing and the
   claim's evidence holds the key. Any other cell shows a dash, and a note under the table says
   what a dash means. The note also links the evidence of every claim that supplied a value.
4. Labels hold no digits outside code spans, so model IDs go in code spans
   (`claude-sonnet-5-5`). The integrity tests check the spec. They also check that the README
   table is what the renderer writes, and that every `num` marker in the README names a
   passing claim.
5. The token comparison (C-022, C-024) adds `per_task_run` to its evidence: each contestant's
   tokens, uncached tokens, hosted tokens and minutes, as means over the shared task runs. The
   table reads these. Minutes are shown although no claim is about time. Stepswitch on local
   models is slower than Claude Code on hosted models, and a reader should see that next to
   the token counts.

## Consequences

- The front page shows the comparison without any hand-typed number, and a claim that stops
  passing takes its values out of the table on the next `ssw prove`.
- The columns name the compared contestants. A column whose claims all fail shows dashes
  rather than disappearing, so a reader sees what is not proven.
- The claims C-021, C-022 and C-023 require a passing canary check since ADR 0037. The Phase 11
  router runs predate the canary, so these claims fail on them until the Phase 12 confirmatory
  runs replace them, and the table shows dashes for them meanwhile.
- SPEC §24 names the results table instead of the leaderboard image.
