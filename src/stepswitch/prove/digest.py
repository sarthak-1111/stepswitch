"""The source digest a prove report is bound to (ADR 0006).

It must equal `source_digest()` in scripts/phase_check.py; a unit test checks that both give
the same value, so phase checks accept only a report produced from the current sources.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

DIGEST_SOURCES = (
    "src",
    "tests",
    "registry",
    "bench/suites",
    "pyproject.toml",
    "uv.lock",
    "claims/claims.yaml",
)
DIGEST_SKIP = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules"}
DIGEST_BINARY_SUFFIXES = {".npz", ".npy", ".png", ".jpg", ".gif", ".gz", ".zip", ".gguf", ".db"}
STATUS_LINE_RE = re.compile(rb"^[ \t]*status:[^\n]*(?:\n|$)", re.M)


def source_digest(root: Path) -> str:
    """sha256 over the files under DIGEST_SOURCES, sorted by POSIX relative path.

    Each entry is `path NUL sha256(content)`. Text is hashed with CRLF normalized to LF, so a
    checkout on any OS gives the same digest. `status:` lines in claims.yaml are dropped first,
    because `ssw prove` rewrites them after computing the digest.
    """
    files: list[Path] = []
    for entry in DIGEST_SOURCES:
        path = root / entry
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(
                p
                for p in path.rglob("*")
                if p.is_file() and p.suffix != ".pyc" and not DIGEST_SKIP & set(p.parts)
            )
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda p: p.relative_to(root).as_posix()):
        rel = path.relative_to(root).as_posix()
        data = path.read_bytes()
        if path.suffix.lower() not in DIGEST_BINARY_SUFFIXES:
            data = data.replace(b"\r\n", b"\n")
        if rel == "claims/claims.yaml":
            data = STATUS_LINE_RE.sub(b"", data)
        digest.update(rel.encode() + b"\0" + hashlib.sha256(data).digest())
    return digest.hexdigest()
