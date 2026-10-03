"""Publishing a content tree to Confluence, driven by the files.

`export-confluence` publishes one document to one page named on the command
line. That is the right shape for a person doing it once and the wrong shape
for a CI job doing it on every merge: the mapping from file to page belongs
in the repository, next to the file, under review — not in a workflow
argument somebody has to keep in step with the tree.

So each document declares its own destination in frontmatter, and this
walks the tree and honours it. A file with no `confluence:` block is not
published at all, which is how a draft stays a draft.

**Dry run is the default**, here as everywhere else that writes to a live
page. The interesting output is the plan: which pages would be created,
which updated, and which skipped and why.

Two guards stand between a merge and a live page:

* **Macros.** A page somebody hand-wrote in Confluence may use `info`,
  `expand`, `status` or page-properties macros, and this project's markdown
  conversion cannot round-trip them — publishing over such a page flattens
  work nobody agreed to lose. Those pages are skipped and named, rather
  than published with a warning printed after the damage.
* **Hand edits.** A publish used to read the live version and increment it,
  so it always won: an edit somebody made on the wiki last week was
  destroyed the next time an unrelated document merged, and nothing said
  so. Now a page is overwritten only when its latest version was written by
  this tool, is the version a person has already pulled into the
  repository, or already says exactly what the repository would publish.
  Anything else has *moved*, and a moved page is reported on its own and
  fails the run — so the pull-and-review loop is triggered rather than
  bypassed. `--force` is there for the decision to overwrite anyway.

The obvious design — store the page version in frontmatter at publish time
and compare — does not work from CI. A publish job runs on a checkout and
cannot commit the new version back, so the recorded number would fall one
behind after every publish and the next one would refuse forever. The
version message this tool stamps on its own writes needs no write-back.

The loop and both guards live in `publisher.py`, written once over a
`LivePage` so that a second kind of store gets the same rules; this module
is the Confluence entry point and keeps the names its callers import.
"""

from __future__ import annotations

from pathlib import Path

from policyforge.export.publisher import (  # noqa: F401 — re-exported for callers
    ADOPTED,
    CREATED,
    MOVED,
    SKIPPED,
    UPDATED,
    ConfluencePublisher,
    PublishReport,
    PublishResult,
    moved_reason,
    publish_documents,
)


def moved_since_last_publish(doc, live) -> str:
    """Why the live Confluence page may hold an edit the repository has not
    seen, or "". `live` is a `ConfluencePage`; the rule is `moved_reason`."""
    return moved_reason(ConfluencePublisher(host=""), doc, ConfluencePublisher.live_page(live))


def unchanged_on_the_wiki(doc, live) -> bool:
    """Whether the live Confluence page already says exactly what `doc`
    would publish. `live` is a `ConfluencePage`."""
    return ConfluencePublisher(host="").same_content(doc, ConfluencePublisher.live_page(live))


def publish_tree(
    root: Path,
    *,
    host: str,
    dry_run: bool = True,
    allow_macros: bool = False,
    only: str = "",
    force: bool = False,
) -> PublishReport:
    """Publish every document in the tree that declares a destination.

    `only` restricts the run to paths containing that substring, which is
    what makes it usable from a CI job that knows which files a merge
    touched rather than republishing the whole set every time. `force`
    overwrites pages that have moved since this tool last wrote them.
    """
    from policyforge.content.tree import load_content_tree

    documents, problems = load_content_tree(root)
    return publish_documents(
        ConfluencePublisher(host=host),
        documents,
        problems,
        dry_run=dry_run,
        allow_unsupported=allow_macros,
        only=only,
        force=force,
    )
