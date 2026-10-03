"""Writes generated documents to output/ as plain markdown.

This is the primary export path (see README's "Output format priority") —
Confluence export (confluence_exporter.py) converts this same output rather
than generating separately. `check_markdown_quality` is the enforcement
mechanism: call it before treating generated output as final.

output/ is gitignored (see .gitignore) since generated policies contain
org-specific context that shouldn't land in this public repo.
"""

from __future__ import annotations

from pathlib import Path

import mdformat

from policyforge.textfile import write_text_lf

#: The same extension set `.pre-commit-config.yaml` gives the mdformat hook,
#: which is the whole point of this function — it exists so a generated file
#: is judged by the rule the repository enforces, and a narrower set here
#: would mean passing locally and failing in CI, or the reverse.
#:
#: `frontmatter` is not optional. Without it mdformat reads a leading `---`
#: as a thematic break and rewrites the YAML underneath it into headings and
#: list items: the stamp `content/provenance.py` writes would be reported as
#: badly formatted, and anything that then *applied* mdformat would destroy
#: it. The pre-commit config has carried that warning since frontmatter
#: first appeared in this repo; this function did not have the extension.
_EXTENSIONS = frozenset({"gfm", "frontmatter"})


def write_markdown(content: str, *, output_dir: Path, filename: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / filename
    # LF on every platform. The gate's `mdformat --check` reads bytes and
    # rejects a CR; `check_markdown_quality` below reads through universal
    # newlines and cannot see one, so it passed the very files the gate
    # then failed. See `policyforge/textfile.py`.
    return write_text_lf(path, content)


def check_markdown_quality(path: Path) -> bool:
    """Checks a generated file against mdformat's formatting rules — the
    same tool enforced repo-wide in pre-commit/CI (see
    .pre-commit-config.yaml). Uses mdformat's Python API directly (not a
    subprocess call) so this can run as part of the generation pipeline
    without shelling out. A generation pipeline should call this before
    treating output as final; a mismatch means the markdown isn't
    well-formed CommonMark and shouldn't be shipped or handed to the
    Confluence exporter.
    """
    original = path.read_text(encoding="utf-8")
    formatted = mdformat.text(original, extensions=_EXTENSIONS)
    return original == formatted
