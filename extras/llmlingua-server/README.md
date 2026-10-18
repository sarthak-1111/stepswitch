# llmlingua-server: reference compression server

An optional, out-of-process server for SPEC §13 TK11 (prompt compression, ADR 0024). It runs
the small LLMLingua-2 checkpoint on the CPU from a local directory. Stepswitch sends it only
documentation text (`.md`, `.markdown`, `.rst`, `.txt`, `.adoc` reads), never code, and only
when compression is switched on. It is off by default.

Torch and llmlingua stay out of Stepswitch's own environment. The server needs nothing else
from Stepswitch: `server.py` uses the standard library and imports `llmlingua` when the model
loads.

## Contract

```
POST /compress   {"text": str, "rate": float, "force_tokens": [str]}   ->  {"text": str}
GET  /health                                                            ->  {"status": "ok", "model": str}
```

`rate` is the share of tokens to keep (0 < rate <= 1). Errors answer `{"error": str}` with a
4xx or 5xx status. Request text never appears in logs or error answers.
`tests/unit/test_llmlingua_server.py` checks this server against Stepswitch's client
(`stepswitch.context.compress.HttpCompressor`).

## Setup

1. Make an environment for the server, separate from Stepswitch's:

   ```
   uv venv .venv-lingua --python 3.12
   uv pip install --python .venv-lingua "llmlingua==0.2.2"
   ```

   On Linux the default torch wheel brings GPU libraries with it. Install a CPU-only torch
   first if you want a smaller environment.

2. Download the checkpoint `microsoft/llmlingua-2-bert-base-multilingual-cased-meetingbank`
   (Apache-2.0, 177M parameters, 709 MB) into a local directory. It needs `config.json`,
   `model.safetensors`, `tokenizer.json`, `tokenizer_config.json`, `special_tokens_map.json`
   and `vocab.txt`. The revision checked for Phase 5 is recorded in docs/RESEARCH.md.

   Name the directory `llmlingua-2-bert-base-multilingual-cased-meetingbank`. llmlingua 0.2.2
   decides how tokens join into words by looking for `bert-base-multilingual-cased` or
   `xlm-roberta-large` in the model name, and fails every request for any other name. The
   server refuses to start with such a directory.

3. Run the prefetch once. It allows downloads, loads the model, compresses one sentence and
   exits:

   ```
   .venv-lingua/bin/python server.py --model-path /models/llmlingua-2-bert-base-multilingual-cased-meetingbank --prefetch
   ```

   (On Windows, use `.venv-lingua\Scripts\python`.) The LLMLingua constructor loads a BPE file
   through tiktoken. Set `TIKTOKEN_CACHE_DIR` to keep that file in a known place, and set it
   the same way when serving.

4. Serve:

   ```
   .venv-lingua/bin/python server.py --model-path /models/llmlingua-2-bert-base-multilingual-cased-meetingbank --port 8095
   ```

   By default the server binds to 127.0.0.1 and runs offline:
   - the model-hub libraries are put in offline mode;
   - the process refuses connections and name lookups for anything but loopback addresses;
   - `--model-path` must be a local directory with a `config.json`.

   `--allow-downloads` lifts the guard.

5. Point Stepswitch at it in `stepswitch.toml` or `.stepswitch/local.toml`:

   ```toml
   [context.compression]
   enabled = true
   endpoint = "http://127.0.0.1:8095"
   rate = 0.5
   ```

Requests go through `stepswitch.net`, so they are audited, and loopback is allowed even in
offline mode. If the server is down or answers an error, the read keeps its original text.

## Status

TK11 stays off because the mini bench suites read almost no prose, so no bench run can show a
gain yet (ADR 0024). The checkpoint was trained on meeting transcripts and may drop identifiers
from technical text. Keep it off for documentation that the task depends on word for word.
