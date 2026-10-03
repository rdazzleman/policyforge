"""What changed on the wiki since this tool last published it.

`publish` already knows: it refuses to overwrite a page whose latest version
this tool did not write, and reports it as moved. But that answer arrives as
the wreckage of a publish somebody was trying to do, which is the wrong time
to learn it and the wrong person to tell. "What has changed on the wiki
since we last published?" is the question a policy owner asks *before* a
review cycle, and answering it used to mean pulling everything and reading
the diff.

So it is a query. Same two rules `publish` uses — a page this tool last
wrote carries its stamp, and a page whose version `pull` recorded has been
seen — and the same content comparison, so a page that says exactly what the
repository says is in sync whoever touched it last.

Writes nothing, and needs no `--apply` to be safe: it reads pages and prints
the `pull` command that would reconcile each one. The reconcile command is
printed rather than run because bringing an edit into the repository is a
diff somebody reviews, not a step a report takes on their behalf.

The query itself is `publisher.drift_documents`, the same loop for every
kind of store; this is its Confluence entry point.
"""

from __future__ import annotations

from pathlib import Path

from policyforge.export.publisher import (  # noqa: F401 — re-exported for callers
    ABSENT,
    IN_SYNC,
    MOVED,
    ConfluencePublisher,
    DriftReport,
    DriftResult,
    drift_documents,
)


def wiki_drift(root: Path, *, host: str, only: str = "") -> DriftReport:
    """Where every published page stands against the tree that owns it.

    `only` narrows the run to paths containing that substring, for the same
    reason `publish` has it: a scheduled job that knows which documents a
    team owns should not read the whole space.
    """
    from policyforge.content.tree import load_content_tree

    documents, _ = load_content_tree(root)
    return drift_documents(ConfluencePublisher(host=host), documents, only=only)
