"""Sphinx configuration of the docs site (ADR 0032).

Build it with `uv sync --group docs`, then
`uv run python -m sphinx -W --keep-going -b html docs/site docs/site/_build/html`.

The pages are the Markdown files in this folder, read by MyST-Parser. They are public text, so
the claims lint checks them (`lint.public_docs` in claims/claims.yaml). Some pages show a file
from elsewhere in the repository (`INCLUDED`): the README's quickstart and claims sections, the
task API, the science.toml reference, the compatibility matrix and the examples. The file is
spliced in where the page says `<!-- included -->` when Sphinx reads the page, so the site and
those files stay the same text. Relative links in it are rewritten to resolve from this folder:
links to files become downloads, and links to folders keep only their text.

The site makes no network requests, neither while building nor in the reader's browser: no
intersphinx (it downloads inventories), no math extensions (MathJax would load from a CDN), and
the Furo theme ships its own fonts and scripts.
"""

from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path
from typing import Any

SITE = Path(__file__).resolve().parent
ROOT = SITE.parents[1]
_PROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

project = "Stepswitch"
author = "the Stepswitch contributors"
copyright = "the Stepswitch contributors"  # noqa: A001 (the name Sphinx reads)
release = version = _PROJECT["version"]

extensions = ["myst_parser"]
source_suffix = {".md": "markdown"}
root_doc = "index"
exclude_patterns = ["_build"]

# `[text](#a-heading)` links resolve to MyST's heading anchors.
myst_heading_anchors = 4
# No `dollarmath` or `amsmath`: pages with math would load MathJax from a CDN.
myst_enable_extensions: list[str] = []

html_theme = "furo"
html_title = f"Stepswitch {release}"
html_copy_source = False
html_show_sourcelink = False

PLACEHOLDER = "<!-- included -->"
# page: (file, from the repository root; start and end markers, or None for the whole file)
INCLUDED: dict[str, tuple[str, str | None, str | None]] = {
    "quickstart": ("README.md", "<!-- quickstart:start -->", "<!-- quickstart:end -->"),
    "how-it-works": ("README.md", "<!-- how-it-works:start -->", "<!-- how-it-works:end -->"),
    "claims": ("README.md", "<!-- claims:start -->", "<!-- claims:end -->"),
    "task-api": ("docs/API.md", None, None),
    "science-toml": ("docs/science-toml.md", None, None),
    "compatibility": ("docs/COMPATIBILITY.md", None, None),
    "examples": ("examples/README.md", None, None),
}
EXAMPLES = ROOT / "examples"
LINK_RE = re.compile(r"\[([^\]\n]*)\]\(([^)\s]+)\)")


def relink(text: str, source: Path, anchors: frozenset[str] = frozenset()) -> str:
    """`text`, read from `source`, with its relative links resolving from this folder.

    A link to a folder named in `anchors` points at the section of that name on the page.
    Other folders have no page, so their links keep only the text. So do links to the design
    documents (Markdown files outside this folder), which name their path instead.
    """

    def fix(match: re.Match[str]) -> str:
        label, target = match.group(1), match.group(2)
        if target.startswith(("#", "http://", "https://", "mailto:")):
            return match.group(0)
        path, _, anchor = target.partition("#")
        resolved = (source.parent / path).resolve()
        if resolved.is_dir():
            if resolved.name in anchors:
                return f"[{label}](#{resolved.name})"
            return label
        if resolved.suffix == ".md" and not resolved.is_relative_to(SITE):
            return f"{label} (`{resolved.relative_to(ROOT).as_posix()}`)"
        rel = Path(os.path.relpath(resolved, SITE)).as_posix()
        return f"[{label}]({rel}{'#' + anchor if anchor else ''})"

    return LINK_RE.sub(fix, text)


def section(name: str) -> str:
    """The text a page includes (see INCLUDED), its links rewritten."""
    rel, start, end = INCLUDED[name]
    source = ROOT / rel
    text = source.read_text(encoding="utf-8")
    if start is not None and end is not None:
        begin = text.index(start) + len(start)
        text = text[begin : text.index(end, begin)]
    anchors = frozenset()
    if name == "examples":
        folders = sorted(p.parent for p in EXAMPLES.glob("*/stepswitch.toml"))
        anchors = frozenset(folder.name for folder in folders)
        text += "".join(example(folder) for folder in folders)
    return relink(text.strip() + "\n", source, anchors)


def example(folder: Path) -> str:
    """A section showing one example's files."""
    parts = [f"\n\n## {folder.name}\n"]
    for path in sorted(folder.iterdir()):
        if path.is_file():
            rel = Path(os.path.relpath(path, SITE)).as_posix()
            options = f":language: toml\n:caption: {path.name}\n"
            parts.append(f"\n```{{literalinclude}} {rel}\n{options}```\n")
    return "".join(parts)


def splice(app: Any, docname: str, source: list[str]) -> None:
    if docname in INCLUDED and PLACEHOLDER in source[0]:
        source[0] = source[0].replace(PLACEHOLDER, section(docname))


def setup(app: Any) -> dict[str, Any]:
    app.connect("source-read", splice)
    return {"parallel_read_safe": True}
