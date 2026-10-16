# Edit format: hash-anchored line edits (version 1)

This is the edit format of the built-in agent's `read` and `edit` tools (SPEC §12 item 1,
SPEC §13 TK5). The implementation is `src/stepswitch/context/edits.py`, used by
`src/stepswitch/workers/tools.py`. The tests in `tests/unit/test_context_edits.py` pin the
golden values below.

Design goals:

- A model names the lines it changes by short anchors copied from `read` output. It does not
  reproduce the old text (as search-and-replace needs) and does no line arithmetic (as unified
  diffs need).
- Stale knowledge is caught per anchor. A refused edit changes nothing, and the error shows
  the current lines with fresh anchors, so the next call can succeed without another `read`.
- Small models format JSON loosely, so common variants are accepted (section 4).

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
  file exists. Missing parent directories are created.
- `{"path": "small.py", "text": "..."}` replaces an existing file of at most 100 lines. Larger
  files need anchored edits. SPEC TK5 rules out whole-file rewrites; this exception covers only
  small files.
- `edits` and `text` cannot be combined, and `create` takes only `text`.

## 4. Accepted variants

- Anchors: `"12:ab"`, `"12:AB"`, `"L12:ab"`, `"12#ab"`, a whole copied row `"12:ab|  text"`
  (everything after `|` is ignored), and `{"line": 12, "hash": "ab"}`. A bare line number is
  refused with a hint to copy the full anchor.
- Op key: `op`, `action`, `type` or `kind`. Op names are matched case-insensitively, with `-`
  and spaces read as `_`. Aliases:
  - `replace`: `replace_lines`, `replace_range`, `set`, `change`, `update`, `modify`, `edit`.
  - `delete`: `delete_lines`, `remove`, `del`, `cut`.
  - `insert_after`: `after`, `append`, `add_after`, `insert_below`.
  - `insert_before`: `before`, `prepend`, `add_before`, `insert_above`.
- Start key: `start`, `from`, `anchor`, `line`, `at`, `begin`, `start_anchor` or `first`.
- End key: `end`, `to`, `until`, `stop`, `end_anchor` or `last`.
- `range`: `"12:ab-14:cd"`; `..`, `,` or an en dash also work as the separator. A two-item list
  works too.
- Text key: `text`, `content`, `new_text`, `replacement`, `code`, `lines`, `new` or `with`.
- `{"after": anchor, "text": ...}` and `{"before": anchor, "text": ...}` are inserts. An edit
  with an anchor and text but no op is a `replace`. A plain `insert` needs `after` or
  `before`.
- `edits` can be a list, a single edit object, or a JSON string holding either.

Anything else is refused with a short message that names the problem and, where it helps,
shows an example.

## 5. Validation

Nothing is written until every check passes:

1. Each anchor's line exists, and its hash matches the line's current hash.
2. If the toolbox showed this `(line, hash)` pair, the line still holds the content it showed.
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
