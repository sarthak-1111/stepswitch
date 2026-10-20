# ADR 0038: Edit format version 2: the edit tool takes the shapes models send

- Status: accepted
- Date: 2026-10-20
- Phase: 12

## Context

ADR 0037 found that 77 of the router's 151 edit calls in the Phase 11 rerun were refused, and
each refusal cost a model call. The step logs give the reasons. 8 calls were refused for their
content: a stale anchor, overlapping edits, or `create` on an existing file. The other 69 were
refused for their shape:

- a patch in the `*** Begin Patch` format, given as `patch` or with no `path` at all;
- old and new text (`old`/`new`, `search`/`replace`, `delete`/`insert` holding text);
- anchors without their line number (`"rv"`) or without their hash (`13`), the hash given
  apart (`{"hash": "cp", "line": 13}`), or a hash with the copied row (`"gf|  text"`);
- an empty `text` beside `edits`, deletes as `{"delete": 1, "line": 19}` or
  `{"delete": true}`, inserts with `"position": "after"`, `line_start` and `line_end`;
- one edit at the top level, without `path`.

gpt-oss:120b wrote these shapes whatever the tool description said. Its own repository ships
an `apply_patch` tool in the `*** Begin Patch` format (docs/RESEARCH.md, Phase 12). Version 1
of the edit format (ADR 0018) refused bare line numbers and bare hashes on purpose: a model
counting lines from a stale copy would change the wrong line.

## Decision

Edit format version 2 (spec/edit-format.md) accepts these shapes wherever the change they ask
for is not in doubt, and refuses with a precise message where it is:

1. **Partial anchors.** A bare hash names the one line that has it now; several or none is
   refused. A bare line number is taken only when the toolbox showed that line with the content
   it holds now, so it cannot have been counted from a stale copy. A hash given apart from its
   line number is joined to it.
2. **Excerpt edits.** Old text names its lines. It is matched as whole lines: exactly, then
   whatever the indentation (the new lines get the same shift), then also reading typographic
   punctuation as ASCII. Failing that, it may match as text inside lines, at word boundaries,
   as a string replace would. Every match must be unique, or a line hint must pick one.
3. **Patches.** The `*** Begin Patch` format and unified diffs are read with the grammar and
   the forward search of the reference tools. Context lines are kept from the file. Where the
   reference tools would guess, Stepswitch refuses: a hunk that matches twice without an `@@`
   line or a line hint, deleting or moving a file, and adding one that exists. Every file is
   checked before any is written.
4. **Defaults and slips.** Empty defaults beside the real argument are left out. A stray
   `create` beside edits is ignored. `create` with the whole content of a file the model has
   read is the small-file rewrite that ADR 0018 already allows. A delete of lines that a
   replace in the same call changes is dropped. An insert without an anchor goes where the
   call's deletes remove one contiguous block, and nowhere else. `new` without `old` is never
   taken as a whole file.
5. **No path.** When `path` is missing, the edit goes to the one file read so far that every
   full anchor and old text fits. The reply says so. A partial anchor never names a file.
6. **Schema and description.** The tool schema lists `old`, `new` and `patch`, and its
   description names them. The whole toolset stays within the size its tests allow (about
   1,200 tokens). `apply_patch` is an alias of the `edit` tool.
7. **SPEC §13 TK5** lists the version 2 kinds: hash-anchored edits, excerpt edits and patches.
   All of them change only the lines they name, so TK5's rule against whole-file rewrites
   stands. Its small-file exception (ADR 0018) is unchanged.

Anchored edits keep all their version 1 checks. Lines found by their text skip the
shown-content check, because their current text has just been matched.

## Evidence

`tests/unit/test_edit_shapes.py` holds one test for each shape above, taken from the run's
arguments, and one for each refusal. The refused calls' arguments were also replayed through
version 2's parsing; the files they edited are gone, so only the shape is checked. Of the 69
calls refused for their shape, version 2 reads 68:

- 4 of them are long patches whose logged arguments are cut at 2,000 characters;
- the one it still refuses sends both an edit and a whole-file `text` that disagree.

Whether this lowers the failed calls per task is measured on the dev suite and in the
confirmatory runs (ADR 0037), not here.

## Consequences

- A refused shape now costs a call only when the request is ambiguous. In that case the error
  says how to fix it and shows the current lines with their anchors.
- Matching inside lines can still pick an unintended place, if the old text happens to be a
  unique piece of another line. Word boundaries make this less likely. The risk is the same as
  for any string-replace tool, and the diff in the reply shows the change.
- Version 1 calls mean the same in version 2, except bare line numbers, which version 1
  refused.
