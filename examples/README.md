# Examples

Each folder holds two files:

- `stepswitch.toml` is the project policy, committed to your repository;
- `local.toml.example` holds the settings of your machine (endpoints, keys, paths). Copy it
  to `.stepswitch/local.toml`, which is gitignored, and edit it.

`tests/unit/test_examples.py` loads every example with the real configuration loader, so they
stay valid.

| Folder | For |
|---|---|
| [cpu-laptop](cpu-laptop/) | A CPU-only laptop with 8 GB of RAM, using the tiny tier |
| [ollama-workstation](ollama-workstation/) | A GPU workstation that runs Ollama |
| [remote-endpoint](remote-endpoint/) | A hosted chat-completions endpoint next to local models, with redaction and a deny list |
| [science-project](science-project/) | A science-track project: golden cases with tolerances, and protected constants |
| [slow-lane](slow-lane/) | A very large model on AirLLM or KTransformers, for asynchronous reviews (`ssw jobs`) |

To use one:

1. Copy its `stepswitch.toml` into your repository (and `science.toml`, for the science
   example).
2. Copy `local.toml.example` to `.stepswitch/local.toml` and edit it.
3. Run `ssw doctor` to check the result.
