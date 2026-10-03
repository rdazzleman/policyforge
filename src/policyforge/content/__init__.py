"""The markdown content tree: an organization's documents as files.

PolicyForge started with Confluence as the place documents *live* and
markdown as an intermediate the generator happened to emit. This package is
the other arrangement: markdown files in a git repository are the source of
truth, and Confluence is a publishing target fed from them.

That inversion buys the things a wiki cannot. A pull request is a review
gate with named approvers and a diff. `git log` is a version history nobody
can quietly rewrite. A branch is a draft that does not confuse anyone
reading production. And a document set that lives in a repo can be checked
by CI — before it is published, rather than after somebody notices.

Nothing here talks to Confluence. Reading the tree, resolving a document's
tier and owner, and knowing which published page a file corresponds to are
all questions with local answers, and keeping them local is what lets the
whole content model be exercised without credentials.
"""
