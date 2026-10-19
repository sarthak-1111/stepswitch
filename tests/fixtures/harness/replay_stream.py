"""A stand-in harness for the contract tests: prints a recorded stdout transcript.

usage: python replay_stream.py <transcript.jsonl> [exit code]

Reads stdin to the end first (the prompt, for adapters that send it there), then writes every
line of the transcript to stdout and exits with the given code (default 0).
"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    path = sys.argv[1]
    code = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    if sys.stdin is not None:
        sys.stdin.read()
    with Path(path).open(encoding="utf-8") as transcript:
        for line in transcript:
            sys.stdout.write(line.rstrip("\r\n") + "\n")
            sys.stdout.flush()
    return code


if __name__ == "__main__":
    sys.exit(main())
