"""Reading a directory of markdown documents into something addressable.

A document on disk is a file; a document in a governance program is a thing
with a tier, an owner and a published location. This module is the mapping
between the two, and it is deliberately tolerant about how much of that a
file actually declares.

Frontmatter is the explicit form:

    ---
    title: Access Control Standard
    tier: standard
    topic: Access Control
    owner: Security Engineering
    confluence:
      space: ENG
      title: Access Control Standard
    ---

Nothing requires it. A file with no frontmatter still resolves: the tier
comes from the directory it sits in (`standards/` -> standard, the layout
`policyforge generate` already writes), the slug from the filename, and the
title from the first H1. That matters because the documents this project has
already generated carry no frontmatter at all, and a content model that only
works for files written after it existed is a migration nobody performs.

Where both are present, frontmatter wins. It is the only place a document
can say something the filesystem cannot express — which published page it
corresponds to, most importantly, since a repo path and a Confluence title
are not the same string and pretending otherwise is how a publish lands on
the wrong page.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

#: Directory name -> tier. **The one table** for where a tier's documents
#: live: `check` reads it one way and every writer (`generate`,
#: `import-confluence`, `pull`) reads it the other, through
#: `TIER_DIRECTORY`. Until #411 this comment said these were "the names
#: `policyforge generate` already writes into"; generate spelled its own
#: directory `f"{tier}s"`, so every generated Policy went to `policys/`,
#: which nothing here reads.
TIER_DIRS = {
    "policies": "policy",
    "standards": "standard",
    "procedures": "procedure",
}

#: Tier -> its directory, for every writer (#411).
TIER_DIRECTORY = {tier: directory for directory, tier in TIER_DIRS.items()}

#: Directories an earlier version wrote that no reader here recognises ->
#: where their documents belong. `check` names them rather than passing
#: them over, and `import-confluence` still reads a predecessor there, so
#: an existing tree's content class is not lost in the move (#411).
LEGACY_TIER_DIRS = {"policys": "policies"}

#: Directories that are never documents. `synthesis/` holds requirement
#: lists the generator consumes rather than anything anyone reads as a
#: document, and dotted directories are this tool's own state.
EXCLUDED_DIRS = {"synthesis", "edits"}

_HEADING_RE = re.compile(r"^#[ \t]+(?P<title>.+?)[ \t]*$", re.MULTILINE)
_MD_LINK_RE = re.compile(r"\[[^\]]*\]\((?P<target>[^)\s]+)(?:[ \t]+\"[^\"]*\")?\)")
_WIKILINK_RE = re.compile(r"\[\[(?P<target>[^\]|]+?)(?:\|[^\]]*)?\]\]")


class ContentError(ValueError):
    """Raised when a file cannot be read as a document.

    Names the path, because the caller's job on catching this is to tell
    somebody which file to go and look at.
    """


@dataclass
class ContentDocument:
    """One markdown file, resolved."""

    path: Path
    #: Path relative to the content root, with forward slashes on every
    #: platform. This is the citation target, so it has to be the string a
    #: reader can paste into a repo URL rather than a Windows-flavoured one.
    relative_path: str
    title: str
    body: str
    tier: str = ""
    slug: str = ""
    metadata: dict = field(default_factory=dict)

    @property
    def targets(self) -> dict:
        """Every destination this file declares, keyed by kind of store.

        The general spelling is a `targets:` block with one entry per kind:

            targets:
              confluence: {space: SEC, title: Access Review Standard}

        `confluence:` at the top level — the spelling every existing tree
        uses — is an alias for `targets.confluence`, and keeps working. A
        file that carries both is read from the top-level block, and
        `check` reports the two when they disagree, so the alias can never
        silently mean two different pages.
        """
        block = self.metadata.get("targets")
        targets = (
            {str(kind): entry for kind, entry in block.items() if isinstance(entry, dict)}
            if isinstance(block, dict)
            else {}
        )
        legacy = self.metadata.get("confluence")
        if isinstance(legacy, dict):
            targets["confluence"] = legacy
        return targets

    def target(self, kind: str) -> dict:
        """The declared destination of one kind, or {} when there is none."""
        return self.targets.get(kind) or {}

    @property
    def confluence(self) -> dict:
        """The `confluence:` frontmatter block, if this file declares one,
        under either spelling."""
        return self.target("confluence")

    @property
    def space(self) -> str:
        return str(self.confluence.get("space") or "")

    @property
    def page_title(self) -> str:
        """The published page's title, which need not match the document's.

        Falls back to the document title: for a document that has never been
        published, that is the best guess available, and a publish would
        create the page under it.
        """
        return str(self.confluence.get("title") or self.title)

    @property
    def page_id(self) -> str:
        return str(self.confluence.get("page_id") or self.confluence.get("id") or "")

    @property
    def topic(self) -> str:
        return str(self.metadata.get("topic") or "")

    @property
    def owner(self) -> str:
        return str(self.metadata.get("owner") or "")

    @property
    def references(self) -> list[str]:
        """Other documents this one links to, as written.

        Both markdown links and `[[wikilinks]]` are read, since a repo of
        governance documents tends to accumulate both. Anchors and external
        URLs are dropped: the question this answers is "what else should I
        read alongside this?", and an https link to a vendor's docs is not
        an answer to it.
        """
        found: list[str] = []
        for match in _MD_LINK_RE.finditer(self.body):
            target = match.group("target").split("#", 1)[0].strip()
            if target and "://" not in target and target not in found:
                found.append(target)
        for match in _WIKILINK_RE.finditer(self.body):
            target = match.group("target").strip()
            if target and target not in found:
                found.append(target)
        return found


def first_heading(body: str) -> str:
    """The document's first H1, or an empty string when it has none."""
    match = _HEADING_RE.search(body)
    return match.group("title").strip() if match else ""


def _title_from_slug(slug: str) -> str:
    return " ".join(word for word in re.split(r"[-_]+", slug) if word).title()


def _tier_from_path(path: Path, root: Path) -> str:
    """The tier implied by any directory between the root and the file.

    Walks the whole relative path rather than only the immediate parent, so
    that both `standards/access-control.md` and a nested
    `access-control/standards/current.md` resolve.
    """
    try:
        relative = path.relative_to(root)
    except ValueError:
        relative = Path(path.name)
    for part in relative.parts[:-1]:
        tier = TIER_DIRS.get(part.lower())
        if tier:
            return tier
    return ""


def parse_document(text: str, *, path: Path, root: Path) -> ContentDocument:
    """Resolve one markdown file's text into a document.

    Frontmatter is authoritative where present; everything it omits is
    inferred from the file's location and content.
    """
    import frontmatter

    try:
        parsed = frontmatter.loads(text)
    except Exception as exc:  # the YAML parser raises several distinct types
        raise ContentError(f"frontmatter is not valid YAML: {exc}") from exc

    metadata = dict(parsed.metadata)
    body = parsed.content
    slug = str(metadata.get("slug") or path.stem)
    title = str(metadata.get("title") or first_heading(body) or _title_from_slug(slug))

    try:
        relative_path = path.relative_to(root).as_posix()
    except ValueError:
        relative_path = path.name

    return ContentDocument(
        path=path,
        relative_path=relative_path,
        title=title,
        body=body,
        tier=str(metadata.get("tier") or _tier_from_path(path, root)),
        slug=slug,
        metadata=metadata,
    )


def render_document(metadata: dict, body: str) -> str:
    """Serialize frontmatter plus body, the way a file in the tree looks.

    The inverse of `parse_document`, used when a live page is pulled down
    into the tree: the binding back to the page it came from has to be
    written into the file, or the next publish would not know where it goes.

    Keys are emitted in the order given rather than sorted, because a human
    opens this file and `title` belongs above `confluence:`. Empty values
    are dropped so a pulled document does not arrive full of blank fields
    nobody filled in.
    """
    import yaml

    kept = {key: value for key, value in metadata.items() if value not in ("", None, [], {})}
    if not kept:
        return body.rstrip() + "\n"
    front = yaml.safe_dump(kept, sort_keys=False, allow_unicode=True).strip()
    return f"---\n{front}\n---\n\n{body.strip()}\n"


def load_content_tree(
    root: Path, *, excluded: set[str] | None = None
) -> tuple[list[ContentDocument], list[tuple[str, str]]]:
    """Every markdown document under `root`, plus what could not be read.

    Returns `(documents, problems)` rather than raising, for the same reason
    sync does: one file with a broken frontmatter block should cost you that
    file, not the other forty. Problems are `(path, reason)` pairs for the
    caller to report.

    Directories beginning with `.` are skipped wholesale — that is where
    this tool keeps its own state, and a corpus that ingested its own
    snapshot would be answering questions from a copy of itself.
    """
    skip = EXCLUDED_DIRS if excluded is None else excluded
    documents: list[ContentDocument] = []
    problems: list[tuple[str, str]] = []

    if not root.exists():
        return documents, problems

    for path in sorted(root.rglob("*.md")):
        relative = path.relative_to(root)
        if any(part.startswith(".") or part.lower() in skip for part in relative.parts[:-1]):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            problems.append((relative.as_posix(), f"could not be read: {exc}"))
            continue
        try:
            documents.append(parse_document(text, path=path, root=root))
        except ContentError as exc:
            problems.append((relative.as_posix(), str(exc)))

    return documents, problems
