# HANDOFF v0.1: briefs that hand a coding task to another model

Status: version 0.1, published with Stepswitch (SPEC §9). The JSON Schema is
[`handoff-v0.1.schema.json`](handoff-v0.1.schema.json) (JSON Schema draft 2020-12). The
implementation is `src/stepswitch/context/handoff.py`; `tests/unit/test_handoff.py` pins the
rendering below.

A brief is a small structured summary of a task in progress. A model reads it instead of the
whole transcript when the transcript is too long to re-read: when a task moves to another model
whose server has not cached the transcript (SPEC §7.2), or when a transcript reaches 60% of its
budget and its older turns are replaced (SPEC §13 TK6). A brief must stay at or under
**1,500 tokens**, counted with the receiving model's tokenizer on the Markdown that is sent.

## 1. Fields

Every field is required; lists may be empty. Strings have the length limits given in the
schema.

| Field | Type | Meaning |
|---|---|---|
| `version` | `"0.1"` | This version. |
| `task_id` | string | The task's id. |
| `goal` | string | The task as the user gave it, with its acceptance commands when they were part of it. |
| `track` | `"general"` or `"science"` | The task's track (SPEC §6). |
| `constraints` | strings | Rules the next model must follow: protected paths, track rules, scope. |
| `acceptance.commands` | strings | Commands that must exit with 0 when the task is done. |
| `acceptance.golden` | strings | Golden output files (science track) that must still match. |
| `state.done` | strings | What was done so far, oldest first. |
| `state.decisions` | `{what, why}` objects | Decisions taken and their reasons. |
| `state.open_questions` | strings | What is still unclear. |
| `files` | `{path, symbols, why}` objects | Files that matter, most relevant first, with the symbols in them that matter and why. |
| `failing` | `{check, excerpt}` objects | Checks that failed last, with the lines of their output that show why (verbatim). |
| `budget_left` | `{tokens, seconds}` | What is left of the task's budgets; `null` for no limit. |
| `provenance` | `{from_worker, step, tokenizer, exact}` | The worker that handed over, the step, the tokenizer that counted the brief and whether the count is exact. |

## 2. Writing a brief

1. The handing-over side builds every field from what it recorded (files read and edited,
   commands and their results, the last failing checks, budgets). Paths, symbols, error lines
   and numbers are copied verbatim.
2. A tiny local summarizer model may rewrite `state` from a short work log. Its answer is
   constrained to a JSON Schema with the `done`, `decisions` and `open_questions` keys; an
   answer that fails the schema is dropped and the built `state` is kept.
3. The brief is validated against the schema. An invalid brief is never sent.
4. The brief is rendered (section 3) and counted with the receiving model's tokenizer
   (section 4). While the count is over the limit, content is trimmed in a fixed order, least
   important first: long failure excerpts, older `done` items, symbols, later files, older
   decisions, then the goal's tail. A count that is only an estimate must stay under 80% of the
   limit.

## 3. Rendering

The receiving model gets the brief as Markdown, in this order. A section whose lists are empty
is left out. `provenance` is metadata for logs and is not rendered.

````text
# Handoff: <task_id>

Goal: <goal>
Track: <track>

## Constraints
- <constraint>

## Acceptance
- command: `<command>`
- golden output: <path>

## Done so far
- <done>

## Decisions
- <what>: <why>

## Open questions
- <question>

## Files
- `<path>` (<symbol>, <symbol>): <why>

## Failing checks
### <check>
```text
<excerpt>
```

Budget left: <tokens> tokens, <seconds> seconds
````

A fence around an excerpt uses one more backtick than the longest run of backticks inside it.
`Budget left` names only the budgets that have a limit. The text ends with a newline.

## 4. Counting tokens

The count is of the rendered Markdown, without special tokens added. The tokenizer is resolved
in this order, and `provenance.tokenizer` names the one used:

1. the receiving model's own `tokenizer.json` from its registry entry (`tokenizer.json:<name>`);
2. the `POST /tokenize` route of the llama.cpp server that serves it (`llama.cpp:/tokenize`);
3. a tokenizer file mapped from the model's name, prefetched at install time (`mapped:<name>`);
4. an estimate of four characters per token (`estimate:chars/4`, `exact: false`).

Nothing is downloaded while a task runs.

## 5. Example

```json
{
  "version": "0.1",
  "task_id": "t20261019-101500-a1b2c3",
  "goal": "Fix the off-by-one error in pager.first_page so that the first page holds page_size items.",
  "track": "general",
  "constraints": ["Do not change files under tests/.", "Keep the change small and focused."],
  "acceptance": {"commands": ["python -m pytest -q"], "golden": []},
  "state": {
    "done": [
      "read pager.py lines 1-40",
      "edited pager.py: first_page now slices items[0:page_size]",
      "ran tests: 1 failed, 4 passed"
    ],
    "decisions": [
      {"what": "Change the slice, not the caller", "why": "Two callers rely on first_page."}
    ],
    "open_questions": ["Should page_size 0 raise ValueError?"]
  },
  "files": [
    {"path": "pager.py", "symbols": ["first_page", "Pager"], "why": "edited"},
    {"path": "tests/test_pager.py", "symbols": ["test_first_page_empty"], "why": "failing test"}
  ],
  "failing": [
    {
      "check": "test",
      "excerpt": "FAILED tests/test_pager.py::test_first_page_empty - IndexError: list index out of range"
    }
  ],
  "budget_left": {"tokens": 312000, "seconds": 1420.5},
  "provenance": {"from_worker": "managed/qwen3-4b-instruct-2507", "step": 9, "tokenizer": "llama.cpp:/tokenize", "exact": true}
}
```

## 6. Versioning

A brief names its version. Readers reject versions they do not know. A later version may add
optional fields; removing or renaming a field, or changing a field's meaning, needs a new major
version.
