# Edit format: hash-anchored line edits (version 2)

This is the edit format of the built-in agent's `read` and `edit` tools (SPEC §12 item 1,
SPEC §13 TK5). The implementation is `src/stepswitch/context/edits.py` and, for patches,
`src/stepswitch/context/patch.py`, used by `src/stepswitch/workers/tools.py`. The tests in
`tests/unit/test_context_edits.py` pin the golden values below; `tests/unit/test_edit_shapes.py`
covers the version 2 shapes (ADR 0038).

Design goals:

- A model names the lines it changes by short anchors copied from `read` output. It does not
  reproduce the old text (as search-and-replace needs) and does no line arithmetic (as unified
  diffs need).
- Stale knowledge is caught per anchor. A refused edit changes nothing, and the error shows
  the current lines with fresh anchors, so the next call can succeed without another `read`.
- Small models format JSON loosely, so common variants are accepted (section 4).
- Models trained on other edit tools send old and new text, or a patch, whatever the tool's
  description says. Version 2 accepts those where the change they ask for is not in doubt
  (sections 4.2 to 4.4), and refuses with a precise message where it is.

## 1. Reading: anchored rows

`read` prints a header and then one row per line:

```text
src/app.py lines 1-3 of 3
1:rc|def f():
2:wn|    return 1
3:fy|
```

A row is `<line>:<hash>|<text>`. The line number is 1-based. The hash is two lowercase letters,
and the text is the line without its line ending. A line longer than 500 characters is shown
cut, followed by `... [N more chars]`. Such a line can still be replaced or deleted through its
anchor. When the output budget ends early, a last row `[more: read with start=N]` says where
to continue. A file of at most 60 lines is always shown whole, whatever range is asked; the
header then ends with `(the whole file: short files are shown whole)`.

## 2. The anchor hash

For each line of the file, in order:

1. `content` is the line without its line ending, with trailing space, tab, CR, FF and VT
   characters removed. Leading whitespace is kept, so indentation changes the hash.
2. `ordinal` is 1 plus the number of earlier lines in the file with the same `content`.
3. `data` is `content` encoded as UTF-8. When `ordinal > 1`, a NUL byte and the ordinal in
   ASCII decimal are appended (`"}"` the third time becomes `}\x003`).
4. `v` is the first 4 bytes of SHA-256(`data`), read as a big-endian unsigned integer, modulo
   676 (26 squared).
5. The hash is `alphabet[v // 26] + alphabet[v % 26]`, where `alphabet` is `a` to `z`.

Golden values:

| line content | ordinal | hash |
|---|---|---|
| `def f():` | 1 | `rc` |
| `    return 1` | 1 | `wn` |
| `x = 2` (also `x = 2 \t`) | 1 | `zq` |
| empty line | 1 | `fy` |
| empty line | 2 | `vy` |
| `}` | 3 | `pg` |

**Why two letters.** An anchor is repeated in every row the model reads and every edit it
writes, so its cost in tokens matters for small models. Two lowercase letters stay short in
any tokenizer and give 676 values. Two checks stop a collision from turning into a wrong
edit:

- **Ordinals.** Repeated lines (blank lines, lone braces, `else:`) are the lines most likely
  to sit at a shifted line number. The ordinal gives each copy a different hash, so a line
  that moved does not silently match another copy of itself.
- **Shown content.** The toolbox records the content behind every anchor it showed. An anchor
  whose line now holds different content is refused even when the two-letter hash happens to
  match.

What remains is an anchor the model did not get from this toolbox, pointing at a line whose
new content collides by chance. That case has 1 chance in 676 per anchor.

Anchors depend only on content and line number. Edits elsewhere in the file do not invalidate
an anchor, unless they shift its line number.

## 3. The `edit` call

```json
{"path": "src/app.py", "edits": [
  {"op": "replace", "start": "12:ab", "end": "13:cd", "text": "    total = a + b\n    return total"},
  {"op": "insert_after", "start": "20:qx", "text": "\ndef helper():\n    return 0"},
  {"op": "insert_before", "start": "1:kd", "text": "import os"},
  {"op": "delete", "start": "30:mm", "end": "31:np"}
]}
```

| op | effect |
|---|---|
| `replace` | Lines `start`..`end` (inclusive; `end` defaults to `start`) become `text`. The new text can have more or fewer lines; an empty `text` removes the lines. |
| `delete` | Lines `start`..`end` are removed. |
| `insert_after` | The `text` lines go after the line `start` names. |
| `insert_before` | The `text` lines go before the line `start` names. |

Every anchor in one call refers to the file as it was **before** the call. Edits are applied
together in one pass, so the model never adjusts line numbers for its own earlier edits in the
same call. Several inserts at the same place keep their order in the request.

**Text.** `text` is the new lines, with indentation written out literally. A trailing newline
is optional: `"a\nb\n"` and `"a\nb"` are both two lines. `""` means no lines and `"\n"` means
one empty line. A list of strings is also accepted. CRLF inside `text` is read as LF.

**Whole files.**

- `{"path": "new.py", "text": "...", "create": true}` creates a file. It is refused if the
  file exists and the model has not read it. Missing parent directories are created.
- `{"path": "small.py", "text": "..."}` replaces an existing file of at most 100 lines. Larger
  files need anchored edits. SPEC TK5 rules out whole-file rewrites; this exception covers only
  small files. `create: true` with the whole content of a file the model has read is the same
  rewrite (version 2).
- `edits` and `text` cannot be combined. An empty `edits` (`[]`, `{}`, `""`) beside `text`, or
  an empty `text` beside `edits`, is a default the model filled in and is left out (version 2).
- `create` takes only `text`; beside `edits` to an existing file it is ignored (version 2).
- `new` without `old` creates a missing file from it, and is refused for an existing file:
  taking it as the whole file could wipe out the rest.

## 4. Accepted variants

### 4.1 Anchored edits

- Anchors: `"12:ab"`, `"12:AB"`, `"L12:ab"`, `"12#ab"`, a whole copied row `"12:ab|  text"`
  (everything after `|` is ignored), and `{"line": 12, "hash": "ab"}`.
- Partial anchors (version 2). A bare hash `"ab"` (or `"ab|  text"`) names the one line that
  has that hash now; a hash that matches no line or several is refused, and the error lists
  the matches. A bare line number `12` is taken only when the toolbox showed line 12 with the
  content it holds now; otherwise it is refused with that line's current anchor, so the model
  cannot have counted from a stale copy. A hash and a line number given apart (`"hash"`, or a
  bare-hash `anchor` beside a numeric `line`) are joined into one anchor.
- Op key: `op`, `action`, `type` or `kind`. Op names are matched case-insensitively, with `-`
  and spaces read as `_`. Aliases:
  - `replace`: `replace_lines`, `replace_range`, `set`, `change`, `update`, `modify`, `edit`.
  - `delete`: `delete_lines`, `remove`, `del`, `cut`.
  - `insert_after`: `after`, `append`, `add_after`, `insert_below`.
  - `insert_before`: `before`, `prepend`, `add_before`, `insert_above`.
- Start key: `start`, `from`, `anchor`, `line`, `at`, `begin`, `start_anchor`, `first`,
  `line_start` or `start_line`.
- End key: `end`, `to`, `until`, `stop`, `end_anchor`, `last`, `line_end` or `end_line`.
- `range`: `"12:ab-14:cd"`; `..`, `,` or an en dash also work as the separator. A two-item list
  works too.
- Text key: `text`, `content`, `new_text`, `replacement`, `code`, `lines`, `new` or `with`.
- `{"after": anchor, "text": ...}` and `{"before": anchor, "text": ...}` are inserts, and so is
  an `insert` with `"position": "after"` (or `before`, `below`, `above`) and an anchor. An
  `insert` at a line without a position goes before it. An edit with an anchor and text but no
  op is a `replace`.
- Deletes (version 2): `{"delete": 2, "line": 19}` removes two lines from line 19;
  `{"delete": true, "line": 19}` removes line 19; `{"delete": "pb", "line": 11}` names line
  11:pb. With text (`text`, or `insert` holding text) beside it, the new lines take the
  removed lines' place.
- In one call (version 2): a delete of lines that a `replace` also changes is dropped, and an
  `insert` without an anchor goes where the call's deletes remove one contiguous block. Without
  such a block it is refused.
- `edits` can be a list, a single edit object, or a JSON string holding either. One edit can
  also be given at the top level, beside `path`.

### 4.2 Excerpt edits (version 2)

An edit can name its lines by their old text instead of anchors:

```json
{"path": "src/app.py", "edits": [{"old": "    total = a - b", "new": "    total = a + b"}]}
```

- Old-text key: `old`, `old_text`, `old_string`, `old_str`, `search`, `find`, `original`,
  `old_lines`, or `delete` holding text. New-text key: `new`, `new_text`, `new_string`,
  `new_str`, `replace`, `replacement`, `with`, a text key, or `insert`.
- The old text is looked for, in order: as whole lines exactly (trailing whitespace never
  counts); as whole lines whatever their indentation, when the file's lines are indented by one
  consistent amount more or less than the excerpt, and the new lines then get the same shift;
  the same, also reading typographic dashes, quotes and spaces as ASCII; as text inside lines
  that occurs once and does not start or end inside a word (`x = 1` is not found in
  `max = 1`), the way a string replace finds it.
- A match must be unique. A `start` or `line` given beside the old text is a hint that picks
  the nearest of several matches. Otherwise several matches are refused, and the error shows
  them with their anchors.
- `old`/`new` can also be given at the top level, beside `path`.

### 4.3 Patches (version 2)

`{"patch": "..."}` (also `diff`, `patch_text`, `unified_diff`), or the whole argument string
when it is a patch, holds a text patch. The tool names `apply_patch` and `applypatch` are
aliases of `edit`. Two kinds are read:

- The `*** Begin Patch` format: `*** Add File:`, `*** Update File:` (with hunks after `@@` or
  `@@ <a line to find first>`, lines starting with a space, `-` or `+`, and an optional
  `*** End of File`), `*** Delete File:`, ending with `*** End Patch`. The grammar and the
  reference behaviour are in docs/RESEARCH.md (Phase 12).
- Unified diffs: `--- a/<path>`, `+++ b/<path>`, `@@ -l,c +l,c @@`. The old start line is a
  hint only; `--- /dev/null` adds a file.

Hunks are found by their text, never by line numbers: context and `-` lines are the old text,
context and `+` lines the new. The search moves forward through the file hunk after hunk, as in
the reference tools, and an `@@ <line>` header starts it at that line. Matching is exact, then
whatever the indentation, then also reading typographic dashes, quotes and spaces as ASCII, as
the reference tools do; context lines keep the file's own text. Where Stepswitch differs:

- A hunk whose old text matches more than once ahead of the search position is refused unless
  an `@@ <line>` header or a unified line hint chooses; the reference tools take the first
  match.
- After a looser match the added lines get the file's indentation; the reference tools write
  them as given.
- A hunk with only added lines goes after its `@@` line, as in one reference, and at the end of
  the file without one, as in the other (the two differ; docs/RESEARCH.md, Phase 12).
- `*** End of File` prefers the match that ends the file and falls back to the hunk's match
  elsewhere, as the Python reference does.
- Deleting and moving files are refused: the edit tool changes content only. Adding a file
  that exists is refused.
- Lenient: blank lines around the patch, a heredoc wrapper, padded markers, CRLF, a missing
  `*** Begin Patch` or `*** End Patch`, a missing `@@` before the first hunk, and a unified
  `@@ -l,c +l,c @@` header inside the `*** Begin Patch` format.
- A file named twice is changed by its second section as the first left it.
- Every file is checked before any is written. A refused patch changes nothing.

### 4.4 The call (version 2)

- `path` may be left out when the edits fit exactly one file read so far: every full anchor
  was shown for it, and every old text is in it now. The reply says where the edit went. A
  partial anchor names no file, so it never stands in for `path`.
- A missing `path` stands for the one file of its name, for anchored edits and for patches'
  updated files, as for `read`.

Anything else is refused with a short message that names the problem and, where it helps,
shows an example.

## 5. Validation

Nothing is written until every check passes:

1. Each anchor's line exists, and its hash matches the line's current hash. Partial anchors
   are completed first (section 4.1), or refused.
2. If the toolbox showed this `(line, hash)` pair, the line still holds the content it showed.
   Lines found by their old text (sections 4.2 and 4.3) skip this check: their current text
   was just matched, and a two-letter collision with an older read could only refuse them
   wrongly.
3. No two `replace`/`delete` ranges share a line. The error asks for one combined `replace`.
4. No insert lands strictly inside a range that a `replace` or `delete` in the same call
   removes. Inserting before the range's first line or after its last line is fine.

A stale anchor refuses the whole call:

```text
stale anchor 12:ab: line 12 is now 12:qz. The line it named may now be 15:ab. Nothing was
changed. Current lines 9-15:
9:hd|...
...
Use these anchors, or read the file again.
```

The "may now be" hint appears when the stale hash occurs exactly once in the current file. An
anchor past the end of the file reads `the file has N lines`. In a file of at most 100 lines the
error shows every line with its current anchor, not only the lines around the stale one, and
ends with `This file is short: you can also send its whole new content as `text`.`

## 6. Preservation and writing

- **Encoding.** Only UTF-8 text can be edited. A file with a NUL byte in its first 8 KiB is
  binary and is refused, and so is a file that is not valid UTF-8. `read` shows such a file
  with replacement characters and says that `edit` will refuse it. A UTF-8 byte-order mark is
  kept.
- **Line endings.** Every untouched line keeps its own ending (CRLF or LF). New lines use the
  file's dominant style: CRLF when more lines end in CRLF than in LF, otherwise LF.
- **Final newline.** A file keeps its final newline, or its lack of one. A file created from
  `text` ends with a newline.
- **File mode.** The mode is kept. A read-only file is refused.
- **Atomic write.** The new content goes to a temporary file in the same directory, which is
  flushed, fsynced and then renamed over the original. On Windows the rename is retried
  briefly while another process holds the file. A refused or failed edit leaves the original
  untouched and leaves no temporary file behind.
- **No-op.** An edit that produces identical bytes is not written. The reply says
  `no change`.

## 7. The reply

After a successful edit the tool returns:

- A unified diff with 2 lines of context. The headers are `--- a/<path>` and `+++ b/<path>`,
  or `--- /dev/null` for a created file.
- Counts of added and removed lines, and the new line total.
- Each changed region with 2 lines of context as anchored rows (at most 40 rows). These are
  the anchors to use next. Lines below a change that altered the line count have new numbers,
  so the model should use these anchors or read again.

## 8. Prior art

Oh My Pi (MIT) has a hash-anchored edit mode, "hashline". docs/RESEARCH.md describes it this
way:

- `read` shows a 4-hex snapshot tag for the whole file.
- A patch names the file and its tag, then lists line-number operations (replace a range,
  insert, delete).
- A stale tag rejects the patch.
- An option rejects anchors on lines that a prior read did not show in full.

This format was designed independently and shares no code with Oh My Pi. The differences:

- A two-letter hash per line instead of one tag for the file. An edit elsewhere in the file
  only invalidates anchors whose line numbers it shifted.
- Ordinals for repeated lines.
- The check compares the line's current content with the content the toolbox showed. It
  does not only ask whether the line was shown.
- Operations are JSON objects, and the variants in section 4 are accepted.
- Stale-anchor errors carry the current lines with fresh anchors.

## 9. Versioning

Any change to the row syntax, the hash rule or the anchor grammar is a new version of this
document. Update `HASH_LEN` and the related code in `edits.py` and the golden values in the
tests in the same change.

- Version 1 (Phase 2, ADR 0018): anchored edits, as above without the version 2 items.
- Version 2 (Phase 12, ADR 0038): partial anchors, excerpt edits, patches, and the call-level
  defaults marked "version 2" above. The row syntax and the hash rule are unchanged, so every
  version 1 call means the same in version 2, except a bare line number, which version 1
  refused and version 2 takes when the toolbox showed that line as it is now.
