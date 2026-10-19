"""The offline task flow of claim C-006, run as one process tree.

Usage: `python offline_flow.py <project> <record.jsonl> <out.json>` with the recorder settings
in the environment (`egress.recorder_env`). On Linux the test starts this script inside a new
network namespace, so the scripted model server below must live in the same namespace: it runs
here, and every `ssw` command runs as a child of this process under `recorder.py`.

The project (the calc-bug toy repository, already committed) gets `ssw init`, then a local
endpoint (the scripted chat-completions server on 127.0.0.1) and a remote one (a hosted URL that
offline mode must never contact). The task runs with `--offline`; `ssw audit` then prints the
connections and processes it recorded. The results go to `out.json`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from fake_server import ScriptedServer  # noqa: E402

RECORDER = HERE / "recorder.py"
INSTRUCTION = "Fix the bug in add() in calc.py so that the tests pass."
REMOTE_URL = "https://models.example.org/v1"


def ssw(project: Path, record: Path, *args: str) -> dict[str, Any]:
    with record.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"kind": "command", "argv": ["ssw", *args]}) + "\n")
    started = time.perf_counter()
    proc = subprocess.run(
        [sys.executable, str(RECORDER), *args],
        cwd=project,
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=900,
        check=False,
    )
    return {
        "argv": ["ssw", *args],
        "exit_code": proc.returncode,
        "seconds": round(time.perf_counter() - started, 2),
        "stdout": proc.stdout,
        "stderr_tail": proc.stderr.strip().splitlines()[-5:],
    }


def main() -> None:
    project, record, out = (Path(a) for a in sys.argv[1:4])
    commands = []
    with ScriptedServer("local-coder") as server:
        commands.append(ssw(project, record, "init", "--no-detect", "--json"))
        local = project / ".stepswitch" / "local.toml"
        local.write_text(
            local.read_text(encoding="utf-8")
            + f'\n[[endpoints]]\nname = "local"\nurl = "{server.url}"\n'
            f'format = "chat-completions"\nmodels = ["{server.model}"]\n'
            f'\n[[endpoints]]\nname = "hosted"\nurl = "{REMOTE_URL}"\n'
            'format = "chat-completions"\nmodels = ["hosted-coder"]\n',
            encoding="utf-8",
        )
        run = ssw(project, record, "run", INSTRUCTION, "--offline", "--json")
        commands.append(run)
        served = len(server.requests)
        port = int(server.url.rsplit(":", 1)[1].split("/", 1)[0])
    try:
        result = json.loads(run["stdout"])
    except ValueError:
        result = {}
    task = str(result.get("task_id") or "")
    audit = ssw(project, record, "audit", "--json", "--limit", "1000", "--task", task)
    processes = ssw(
        project, record, "audit", "--processes", "--json", "--limit", "1000", "--task", task
    )
    commands += [audit, processes]
    out.write_text(
        json.dumps(
            {
                "commands": [{k: v for k, v in c.items() if k != "stdout"} for c in commands],
                "result": result,
                "audit": json.loads(audit["stdout"] or "{}").get("rows", []),
                "processes": json.loads(processes["stdout"] or "{}").get("rows", []),
                "server": {"port": port, "requests": served},
                "remote_url": REMOTE_URL,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
