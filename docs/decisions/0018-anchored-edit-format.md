# ADR 0018: Hash-anchored edit format, with whole-file text for small files

- Status: accepted
- Date: 2026-10-16
- Phase: 2

## Context

SPEC §12 item 1 asks for Stepswitch's own hash-anchored edit format, specified under `spec/`,
and SPEC TK5 says edits are hash-anchored or unified diffs, "never whole-file rewrites". The
format has to work for small local models: anchors must be cheap in tokens, stale knowledge
must be caught before a wrong line changes, and loosely formatted JSON must still be accepted.

## Decision

1. `spec/edit-format.md` (version 1) is the format; `src/stepswitch/context/edits.py`
   implements it and the unit tests pin its golden values.
2. `read` shows each line as `<line>:<hash>|<text>`. The hash is two lowercase letters (676
   values) from SHA-256 of the line without trailing whitespace; a line that repeats earlier in
   the file gets an ordinal mixed into its hash, so copies differ. Two letters keep the anchor
   short in any tokenizer.
3. An edit names lines by anchors (`replace`, `insert_after`, `insert_before`, `delete`). Every
   anchor is checked against the current file, and the lines the model was shown must still
   match; one stale anchor refuses the whole call, changes nothing, and the error shows the
   current lines with fresh anchors.
4. **Narrowing of TK5.** A whole-file `text` is accepted to create a file, and to replace an
   existing file of at most 100 lines. Larger files need anchored edits. Small models produce
   correct small files far more reliably than correct anchors, and a 100-line file costs about
   as much to rewrite as to re-read; the limit keeps TK5's purpose (no large rewrites that waste
   tokens and lose unrelated content).
5. **Short files are shown whole.** `read` shows a file of at most 60 lines whole, whatever
   range is asked, and a stale anchor in a file of at most 100 lines is answered with the whole
   file and its fresh anchors plus the offer to send `text`. In live runs a 3.8B model that
   saw only part of a short file edited the part it saw; with these two rules it completed the
   C-005 task (docs/RESEARCH.md, "Small models through the fallback protocol").

## Consequences

- A hash collision cannot change a wrong line: the anchor's line number and the shown-content
  check must both agree.
- The agent's tool description and example teach the anchors (ADR 0016 item 4), and the
  system prompt says to read a file again before editing it a second time.
- If small-model benchmarks (Phase 3) show the 100-line limit is wrong in either direction, it
  is a constant in `edits.py` and this ADR is updated.
