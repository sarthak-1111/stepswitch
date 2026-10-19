"""A mock ACP agent for the conformance and contract tests (SPEC §21): replays a transcript.

usage: python mock_agent.py <transcript.jsonl>

The transcript is a client's view of one session, one `{"dir": ..., "msg": ...}` per line (the
format `stepswitch.workers.acp.JsonRpcPeer` records): "send" lines are what the client sends,
"recv" lines what the agent sends. The mock writes each "recv" message to stdout and reads one
message from stdin for each "send" line, which must match it: the string "{any}" matches any
value, and "{cwd}" inside a string stands for the agent's working directory. A mismatch is
reported on stderr and ends the agent with exit code 3. After the transcript, the mock waits
for the client to close stdin.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ANY = "{any}"


def substitute(value: Any, cwd: str) -> Any:
    if isinstance(value, str):
        return value.replace("{cwd}", cwd)
    if isinstance(value, list):
        return [substitute(item, cwd) for item in value]
    if isinstance(value, dict):
        return {key: substitute(item, cwd) for key, item in value.items()}
    return value


def matches(expected: Any, actual: Any) -> bool:
    if expected == ANY:
        return True
    if isinstance(expected, dict):
        return (
            isinstance(actual, dict)
            and set(expected) == set(actual)
            and all(matches(expected[key], actual[key]) for key in expected)
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(expected) == len(actual)
            and all(matches(e, a) for e, a in zip(expected, actual, strict=True))
        )
    return bool(expected == actual)


def fail(message: str) -> int:
    sys.stderr.write(f"mock agent: {message}\n")
    sys.stderr.flush()
    return 3


def main() -> int:
    cwd = str(Path.cwd().resolve())
    with Path(sys.argv[1]).open(encoding="utf-8") as transcript:
        lines = [json.loads(line) for line in transcript if line.strip()]
    for number, line in enumerate(lines, start=1):
        message = substitute(line["msg"], cwd)
        if line["dir"] == "recv":
            sys.stdout.write(json.dumps(message, separators=(",", ":")) + "\n")
            sys.stdout.flush()
            continue
        raw = sys.stdin.readline()
        if not raw:
            return fail(f"line {number}: the client closed stdin; expected {json.dumps(message)}")
        actual = json.loads(raw)
        if not matches(message, actual):
            return fail(
                f"line {number}: expected {json.dumps(message)}\n  got {json.dumps(actual)}"
            )
    while sys.stdin.readline():
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
