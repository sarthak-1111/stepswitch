# ADR 0032: The docs site is built with Sphinx, MyST-Parser and Furo, from docs/site

- Status: accepted
- Date: 2026-10-19
- Phase: 10

## Context

SPEC §24 asks for a docs site whose tool is chosen by an ADR in Phase 10. It notes that
Material for MkDocs is in maintenance mode, that MkDocs 2.0 is incompatible with it, and that
its successor is in alpha.

ADR 0006 already placed the site: public text is `README.md` and `docs/site/**/*.md`, and the
claims lint checks it. The design documents (SPEC, RESEARCH, PROGRESS, ARCHITECTURE, ADRs)
discuss plans, hypotheses and other projects, so they are not linted. The user-facing
references are Markdown files that other code points at: the task API (`docs/API.md`), the
science.toml reference, the generated compatibility matrix and `examples/README.md`. Hard
rule 6 rules out telemetry and any network call the user did not configure. That includes a
docs build on a contributor's machine and the pages a reader opens.

Research checked on 2026-10-19 (docs/RESEARCH.md, "Verified during Phase 10") found:

- **MkDocs 1.x** has had no release since 1.6.1 (2024-08-30). **MkDocs 2.0** exists only as
  pre-releases. It is a rewrite with no plugin API, and it refuses to run on an `mkdocs.yml`.
- **Material for MkDocs** is in maintenance mode. It gets critical and security fixes until
  2027-05-05.
- **Zensical**, its successor, is at 0.0.x, about two releases a week. Its first "dependable"
  0.1 line is planned for 2026-11-05. Its default theme loads web fonts from a font CDN in the
  reader's browser unless `font = false`.
- **Sphinx** 9.1.0, **MyST-Parser** 5.1.0 and **Furo** 2025.12.19 are maintained and pure
  Python, so they install through uv into a dependency group. Sphinx 9.1 needs Python 3.12;
  9.0.4 still supports 3.11.
  - MyST-Parser reads the existing Markdown as it is.
  - The Furo wheel contains no network code and no external fonts.
  - Sphinx's math support loads MathJax from a CDN, on pages that contain math.
- **The Node site generators** (Docusaurus, Starlight, VitePress) would add a second
  JavaScript toolchain next to the dashboard's.
  - Astro's telemetry is on by default, including in CI.
  - The Docusaurus CLI checks the package registry for updates.

## Decision

1. **Sphinx with MyST-Parser and the Furo theme, in `docs/site/`.** `docs/site/conf.py` and
   `docs/site/index.md` are the entry points. The pages are the site's own public text.
2. **Pages that show files from elsewhere are spliced, not copied.**
   - A page that says `<!-- included -->` gets its file when Sphinx reads it (`INCLUDED` in
     `conf.py`, through the `source-read` event). These are the README's quickstart,
     "How it works" and claims sections, between their markers, the task API, the
     science.toml reference, the compatibility matrix and the examples with their files.
   - The site and the README therefore show the same text, and the claims page lists exactly
     the generated, passing claims.
   - Relative links are rewritten to resolve from `docs/site/`:
     - links to files become downloads, such as a claim's evidence file;
     - links to folders and to design documents keep their text and name the path;
     - links to an example's folder point at that example's section.
3. **The included files are public text too.** `lint.public_docs` lists them, so the claims
   lint checks every word the site shows. Their wording was adjusted where it used unmarked
   absolutes ("never" becomes "not"). The design documents stay in the repository, and the
   site's project page names them.
4. **A `docs` dependency group** (`uv sync --group docs`) holds `sphinx>=9.0.4,<10`, which the
   lock resolves to 9.0.4 on Python 3.11 and 9.1.0 from 3.12, along with `myst-parser` 5.1 and
   `furo`.
5. **No network, at build time or in the reader's browser.**
   - No intersphinx, which downloads inventories.
   - No math extensions, so no page loads MathJax.
   - No external fonts or scripts.
   - The Phase 10 check builds the site and fails if a built page or stylesheet references a
     remote script, stylesheet, image, frame or font. Plain hyperlinks are allowed; a reader
     follows them or not.
6. **Warnings are errors.** CI and the phase check build with `-W --keep-going`, so a broken
   link, a page outside the toctree or a code block its lexer cannot read fails the build. The
   `docs` workflow uploads the built HTML as an artifact. Publishing it to a host needs the
   repository's public home and is added with it.

## Consequences

- Contributors need nothing beyond uv to build the docs. The build is offline once the group
  is installed.
- MyST's heading anchors (`myst_heading_anchors`) make `#section` links work on the site.
- A reference page changes in one place, its file, and both the repository and the site show
  the change.
- Moving to Zensical later stays cheap: the pages are plain Markdown and the configuration is
  one file. That can be re-evaluated after its 0.1 line.
- Math in the docs would need a vendored MathJax (`mathjax_path`) before it can be enabled.
