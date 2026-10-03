"""Bringing live pages down into the content tree as markdown.

The inverse of publishing, and the piece that makes the repo model
survivable rather than aspirational. Somebody will always edit the wiki
directly — that is what a wiki is for — and without a way back, every such
edit is either lost on the next publish or quietly makes the repo wrong.
Pulling turns it into a diff on a branch: reviewable, attributable, and
merged like anything else.

Each pulled file is written with the frontmatter binding it back to the page
it came from, so the round trip closes. A repo path and a page title are
different strings, and a pull that did not record the correspondence would
publish to the wrong place, or nowhere.

**Pages this tool cannot round-trip are refused, not degraded.** A page
using `info`, `expand`, `status` or page-properties macros converts to
readable markdown and would be flattened on the way back — so pulling it
would produce a file that looks correct and destroys those macros the first
time it is published. Refusing names the page and the macros and leaves it
alone, which is the only outcome that does not eventually lose somebody's
work.

The loop is `publisher.pull_documents`, the same for every kind of store;
this module is its Confluence entry point and owns where a pulled page
lands in the tree.
"""

from __future__ import annotations

from pathlib import Path

from policyforge.content.tree import TIER_DIRECTORY
from policyforge.export.publisher import (  # noqa: F401 — re-exported for callers
    REFUSED,
    UNCHANGED,
    WRITTEN,
    ConfluencePublisher,
    PullReport,
    PullResult,
    pull_documents,
)

#: Where a pulled document lands when nothing says otherwise. Tier-first,
#: matching what `generate` writes, so a pulled page and a generated one
#: sit beside each other rather than in two parallel hierarchies. The same
#: table `generate` reads since #411; before it, generate wrote Policies to
#: `policys/` and this wrote them to `policies/`.
_TIER_DIR = TIER_DIRECTORY


def target_path(root: Path, *, tier: str, slug: str) -> Path:
    """Where a pulled page belongs in the tree."""
    directory = _TIER_DIR.get(tier)
    return (root / directory / f"{slug}.md") if directory else (root / f"{slug}.md")


def pull_pages(
    pages: list[tuple[str, str, str]],
    *,
    root: Path,
    host: str,
    dry_run: bool = True,
    allow_macros: bool = False,
) -> PullReport:
    """Fetch `(space, title, tier)` triples into the tree.

    Takes an explicit list rather than discovering pages itself, so the
    caller decides what is in scope — a topic's declared set, a whole space,
    or one page somebody hand-edited.
    """
    return pull_documents(
        ConfluencePublisher(host=host),
        pages,
        root=root,
        target_path=target_path,
        dry_run=dry_run,
        allow_unsupported=allow_macros,
    )


def pages_from_topics(topics, *, kind: str = "confluence") -> list[tuple[str, str, str]]:
    """Every page the topic registry declares for one store, as pull targets.

    The container is the store's own: a space key for Confluence, a
    repository for a wiki. A topic declaring no block of that kind
    contributes nothing, which is how a registry mid-migration pulls only
    what it has actually published.
    """
    targets: list[tuple[str, str, str]] = []
    for topic in topics:
        block = topic.target(kind) if hasattr(topic, "target") else (topic.confluence or {})
        container = str((block or {}).get("space") or (block or {}).get("repository") or "")
        pages = topic.pages(kind) if hasattr(topic, "pages") else topic.confluence_pages()
        if not block:
            continue
        # A wiki topic may name no repository, taking it from config; its
        # pages are still pullable, with the container filled in by the
        # caller's publisher.
        if kind == "confluence" and not container:
            continue
        targets.extend((container, title, tier) for tier, title in pages)
    return targets
