"""One interface between the content tree and a live page store.

`publish`, `wiki-drift` and `pull` were written against Confluence, and the
decisions they make — fetch the live page, refuse one this tool cannot
round-trip, refuse one somebody edited since the last publish, write, say
what would reconcile — were written in Confluence's terms: a space key, a
storage-format body, a version number, a version message. None of those
decisions is about Confluence. A GitHub wiki holds the same facts as a
git repository of markdown files: a commit sha where Confluence has a
version number, a commit trailer where it has a version message, the file
itself where it has storage format.

So the decisions live here, once, over a `LivePage`, and everything that
knows how a particular store spells them is a `Publisher`. The Confluence
adapter below wraps `confluence_importer`, `confluence_exporter` and
`confluence_search` unchanged; the loops in `publish.py`, `drift.py` and
`pull.py` are the generic ones and produce, for Confluence, exactly the
reports and the exporter calls they produced before this module existed
(`tests/test_publisher_equivalence.py` holds that, argument for argument).

A document names its destinations in frontmatter under `targets:`, keyed
by kind. `confluence:` at the top level, the spelling every existing tree
uses, is an alias for `targets.confluence` and keeps working as it is:

    targets:
      confluence: {space: SEC, title: Access Review Standard, version: 12}

The three guards, stated once so an adapter cannot half-implement them:

* **Unsupported content.** A live page holding something the round trip
  would flatten — Confluence macros; on a wiki, a page in a format other
  than markdown — is skipped by name rather than published over.
* **Moved.** A live page is overwritten only when its latest version was
  written by this tool, or is the version `pull` recorded in the document,
  or already says what the document says. Anything else has moved, and a
  moved page is reported and fails the run.
* **Dry run.** Every loop plans unless told to apply, and a plan reads
  pages and writes nothing.
"""

from __future__ import annotations

import shlex
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

CONFLUENCE = "confluence"

CREATED = "created"
UPDATED = "updated"
SKIPPED = "skipped"
#: Changed on the live store since this tool last wrote it, by somebody the
#: repository has not heard from. Kept apart from SKIPPED because it asks
#: for a different action: not "read the reason", but "pull and review".
MOVED = "moved"

#: The reason attached to a page taken over from an unstamped publish.
ADOPTED = "adopted: unchanged since a publish from before pages were stamped"

#: Drift states. MOVED is shared with the publish actions above on purpose:
#: it is the same fact, found by a query instead of by a refused write.
IN_SYNC = "in-sync"
ABSENT = "absent"


@dataclass(frozen=True)
class LivePage:
    """What a store holds for one document, in the store's own terms.

    `version` is a string because the two stores this was written for
    disagree about its type: a Confluence version is an integer, a git
    commit is a sha. Guards compare it for equality and print it, and do
    neither arithmetic nor ordering, so a string loses nothing.

    `body` is the page as the store keeps it — storage format, or markdown
    — and only the adapter that produced it reads it, through
    `Publisher.same_content` and `Publisher.as_markdown`.
    """

    version: str
    #: The stamp: the latest version was written by this tool, so nothing
    #: has touched the page since it was last published from here.
    written_by_tool: bool
    url: str
    body: str
    #: Identifier the store assigns, recorded in the binding a pull writes.
    page_id: str = ""
    #: Who last wrote this version, and when, in whatever form the store
    #: knows it ("Dana Okafor on 2026-09-14"). Empty when the store cannot
    #: say — Confluence's importer does not carry it — and the moved report
    #: falls back to "someone other than this tool".
    author: str = ""
    #: What the round trip would flatten — macro names, or a page format —
    #: empty when the page can be published over safely.
    unsupported: tuple[str, ...] = ()
    #: The latest version is a live edit this tool made (`edit-confluence`,
    #: `edit-topic --host`): approved, but not in the repository. Not
    #: `written_by_tool`, so it is refused like a hand edit; this only lets
    #: the report say what it was (#383).
    live_edit: bool = False


class Publisher(ABC):
    """Everything a live page store has to be able to say and do.

    Adapters translate; the loops below decide. An adapter that finds
    itself deciding whether a page may be overwritten is doing the wrong
    job — that rule is `moved_reason`, and it is the same for every store.

    One thing is deliberately *not* generic: what "the page already says
    this" means. `LivePage.body` is the store's native body and
    `same_content` belongs to the adapter, rather than the page carrying a
    `body_markdown` the loop could compare itself, because Confluence
    compares in storage format — a page it reflowed on save is unchanged,
    and a page published before the stamp existed is adopted on that
    comparison — while a git-backed wiki compares bytes, where any
    difference is an edit. Converting a Confluence body to markdown before
    comparing would quietly change which edits count as "the wiki changed
    this", and a guard loosened by tidying is worse than one that looks
    inconsistent across adapters.
    """

    #: The key a document's `targets:` block uses for this store.
    kind: str = ""

    # -- where a document goes ---------------------------------------------

    @abstractmethod
    def location(self, doc) -> str:
        """The store-level container a document publishes into — a
        Confluence space key, a wiki repository — or "" when the document
        declares none, which is how a draft stays a draft."""

    @abstractmethod
    def title(self, doc) -> str:
        """The live page's title, which need not match the document's."""

    @abstractmethod
    def recorded_version(self, doc) -> str | None:
        """The version `pull` wrote into the document, or None."""

    # -- reading -----------------------------------------------------------

    @abstractmethod
    def fetch(self, location: str, title: str) -> LivePage:
        """The live page, or LookupError when there is none."""

    def fetch_for(self, doc) -> LivePage | None:
        """The document's live page, or None when it has not been created."""
        try:
            return self.fetch(self.location(doc), self.title(doc))
        except LookupError:
            return None

    @abstractmethod
    def same_content(self, doc, live: LivePage) -> bool:
        """Whether the live page already says exactly what `doc` would
        publish, compared in the store's own terms."""

    @abstractmethod
    def as_markdown(self, live: LivePage) -> str:
        """The live page's body as markdown, for a pull."""

    @abstractmethod
    def binding(self, location: str, title: str, live: LivePage) -> dict:
        """The frontmatter a pulled page carries so the round trip closes:
        enough for the next publish to find the page, and the version so
        the next publish knows this edit has been seen."""

    # -- writing -----------------------------------------------------------

    @abstractmethod
    def write(self, doc) -> str:
        """Publish `doc` to its target, stamped as this tool's write.
        Returns the page's URL. Never called on a dry run."""

    # -- saying ------------------------------------------------------------

    @abstractmethod
    def reconcile_command(self, doc) -> str:
        """The `pull` invocation that brings the live edit into the tree."""

    def undeclared_note(self, count: int, verb: str) -> str:
        """The report line for documents that declare no target of this kind."""
        return f"{count} document(s) declare no `{self.kind}:` block and {verb}."

    # -- hooks an adapter may take, each defaulting to what happens today ---

    def prepare(self, documents) -> None:  # noqa: B027 - optional hook, not abstract
        """Called once with every in-scope document before the first fetch.

        For a store whose write depends on the whole tree rather than on one
        file: the wiki rewrites cross-references between documents, and can
        only know that `../standards/access-review.md` is a page once it has
        seen which documents declare a wiki target.
        """

    def refuse(self, doc) -> str:
        """Why this document must not be published at all, or "".

        Checked before the page is fetched, because a refusal that depends
        on the document alone should not need a network call to reach —
        and because the reason a document is refused (licensed content to a
        public wiki) must not be silently overtaken by a fetch failure.
        """
        return ""

    def plan_header(self) -> str:
        """One line printed above the report, or "".

        Where a store has a fact the reader must see before reading the
        plan: whether the wiki being published to is public.
        """
        return ""

    def notes(self) -> list[str]:
        """Anything the run should say once, rather than per document."""
        return []


# --------------------------------------------------------------------------
# the guard, once
# --------------------------------------------------------------------------


def moved_reason(publisher: Publisher, doc, live: LivePage) -> str:
    """Why the live page may hold an edit the repository has not seen, or "".

    Safe to overwrite in two cases. The latest version carries this tool's
    stamp, so nothing has touched the page since it was last published from
    here. Or its version is the one `pull` recorded in the document's
    frontmatter, so a person has already brought that edit into the
    repository and reviewed it as a diff.
    """
    if live.written_by_tool:
        return ""
    recorded = publisher.recorded_version(doc)
    if recorded is not None and recorded == live.version:
        return ""

    known = (
        f"the repository last pulled version {recorded}"
        if recorded is not None
        else "the repository has no pulled version on record"
    )
    if live.live_edit:
        by = "last written by a live edit (edit-confluence or edit-topic --host)"
    elif live.author:
        by = f"last written by {live.author}"
    else:
        by = "last written by someone other than this tool"
    return f"the live page is at version {live.version}, {by}, and {known}"


# --------------------------------------------------------------------------
# publish
# --------------------------------------------------------------------------


@dataclass
class PublishResult:
    """What happened, or would happen, to one document."""

    path: str
    #: The store-level container: a Confluence space key. Named for the
    #: store this was first written against; an adapter for another store
    #: puts its own container here.
    space: str
    title: str
    action: str
    reason: str = ""
    url: str = ""


@dataclass
class PublishReport:
    results: list[PublishResult] = field(default_factory=list)
    dry_run: bool = True
    #: Printed above everything, when the store has something the reader
    #: must see before the plan rather than after it.
    header: str = ""
    #: Said once for the run rather than once per document.
    notes: list[str] = field(default_factory=list)
    #: Documents that declared no destination. Counted rather than listed:
    #: in a tree mid-migration this is most of them, and naming each one
    #: would bury the pages that did publish.
    undeclared: int = 0
    #: What the undeclared line says. Set by the loop from the publisher, so
    #: a report reads "no `confluence:` block" for Confluence and names the
    #: other store's key for another store.
    undeclared_note: str = ""

    def _of(self, action: str) -> list[PublishResult]:
        return [r for r in self.results if r.action == action]

    @property
    def skipped(self) -> list[PublishResult]:
        return self._of(SKIPPED)

    @property
    def moved(self) -> list[PublishResult]:
        return self._of(MOVED)

    @property
    def published(self) -> list[PublishResult]:
        return self._of(CREATED) + self._of(UPDATED)

    def format_report(self) -> str:
        verb = "Would publish" if self.dry_run else "Published"
        lines = [self.header] if self.header else []
        unchanged = len(self._of(UNCHANGED))
        lines.append(
            f"{verb} {len(self.published)} page(s): "
            f"{len(self._of(CREATED))} new, {len(self._of(UPDATED))} updated"
            # Said only when there are some: a page left alone because it
            # already matched (#391), not written, so not in the count before.
            + (f"; {unchanged} already matched the page and were left alone" if unchanged else "")
        )
        for result in self.published:
            mark = "+" if result.action == CREATED else "~"
            note = f" ({result.reason})" if result.reason else ""
            lines.append(f"  {mark} {result.path} -> {result.space}/{result.title}{note}")
            if result.url:
                lines.append(f"      {result.url}")

        if self.moved:
            lines += [
                "",
                f"Changed on the wiki since this tool last published them ({len(self.moved)}) "
                "— not overwritten. Pull them, review the change, then publish:",
            ]
            lines += [f"  {r.path}: {r.reason}" for r in self.moved]

        if self.skipped:
            lines += ["", f"Skipped {len(self.skipped)}:"]
            lines += [f"  {r.path}: {r.reason}" for r in self.skipped]

        if self.undeclared:
            lines += [
                "",
                self.undeclared_note
                or f"{self.undeclared} document(s) declare no `confluence:` block and were "
                "left alone.",
            ]

        if self.notes:
            lines += [""] + [f"  {note}" for note in self.notes]

        if self.dry_run and self.published:
            lines += ["", "Nothing was written. Pass --apply to publish."]
        return "\n".join(lines)


def publish_documents(
    publisher: Publisher,
    documents,
    problems,
    *,
    dry_run: bool = True,
    allow_unsupported: bool = False,
    only: str = "",
    force: bool = False,
) -> PublishReport:
    """Publish every document that declares a target of the publisher's kind.

    `problems` are the files `load_content_tree` could not read, reported
    as skipped so a broken frontmatter block costs that file and not the
    run. `only` restricts the run to paths containing that substring, which
    is what makes it usable from a CI job that knows which files a merge
    touched. `force` overwrites pages that have moved since this tool last
    wrote them.
    """
    report = PublishReport(dry_run=dry_run)
    report.results.extend(
        PublishResult(path=path, space="", title="", action=SKIPPED, reason=reason)
        for path, reason in problems
    )

    in_scope = [doc for doc in documents if not only or only in doc.relative_path]
    publisher.prepare(in_scope)

    for doc in in_scope:
        location = publisher.location(doc)
        if not location:
            report.undeclared += 1
            continue
        title = publisher.title(doc)

        # Before the fetch: a document that must not be published does not
        # become publishable because its page could not be read, and does
        # not need the network to say so.
        refused = publisher.refuse(doc)
        if refused:
            report.results.append(
                PublishResult(
                    path=doc.relative_path,
                    space=location,
                    title=title,
                    action=SKIPPED,
                    reason=refused,
                )
            )
            continue

        live = publisher.fetch_for(doc)

        if live is not None and live.unsupported and not allow_unsupported:
            report.results.append(
                PublishResult(
                    path=doc.relative_path,
                    space=location,
                    title=title,
                    action=SKIPPED,
                    reason=(
                        f"the live page uses macros this tool cannot round-trip "
                        f"({', '.join(live.unsupported)}); publishing would flatten them"
                    ),
                )
            )
            continue

        reason = ""
        if live is not None and not force:
            moved = moved_reason(publisher, doc, live)
            if moved and publisher.same_content(doc, live):
                # Nothing to lose: the page already says what the repository
                # says. Publishing it stamps it, and it is tracked from here.
                moved, reason = "", ADOPTED
            if moved:
                report.results.append(
                    PublishResult(
                        path=doc.relative_path,
                        space=location,
                        title=title,
                        action=MOVED,
                        reason=moved,
                        url=live.url,
                    )
                )
                continue

        # A page that already says what the file says is left alone (#391).
        # Writing it anyway made a new version of every page on every run, and
        # the next `pull` rewrote every file's version number. Not for a page
        # being adopted: that write is what stamps it, and an unstamped page
        # would read as a hand edit the first time the file changed.
        if live is not None and reason != ADOPTED and publisher.same_content(doc, live):
            report.results.append(
                PublishResult(
                    path=doc.relative_path,
                    space=location,
                    title=title,
                    action=UNCHANGED,
                    url=live.url,
                )
            )
            continue

        action = UPDATED if live is not None else CREATED
        url = ""
        if not dry_run:
            url = publisher.write(doc)

        report.results.append(
            PublishResult(
                path=doc.relative_path,
                space=location,
                title=title,
                action=action,
                reason=reason,
                url=url or (live.url if live else ""),
            )
        )

    report.undeclared_note = publisher.undeclared_note(report.undeclared, "were left alone")
    report.header = publisher.plan_header()
    report.notes = publisher.notes()
    return report


# --------------------------------------------------------------------------
# drift
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DriftResult:
    """One document, and where its page stands."""

    path: str
    space: str
    title: str
    tier: str
    state: str
    reason: str = ""
    url: str = ""
    #: The command that brings this page's edit into the repository. Built
    #: by the publisher, since only it knows how its `pull` is spelled.
    reconcile: str = ""


@dataclass
class DriftReport:
    results: list[DriftResult] = field(default_factory=list)
    #: Documents declaring no destination — most of a tree mid-migration.
    undeclared: int = 0
    undeclared_note: str = ""

    def _of(self, state: str) -> list[DriftResult]:
        return [r for r in self.results if r.state == state]

    @property
    def moved(self) -> list[DriftResult]:
        return self._of(MOVED)

    @property
    def in_sync(self) -> list[DriftResult]:
        return self._of(IN_SYNC)

    @property
    def absent(self) -> list[DriftResult]:
        return self._of(ABSENT)

    def format_report(self) -> str:
        lines = [
            f"{len(self.moved)} page(s) changed on the wiki since this tool last "
            f"published them; {len(self.in_sync)} in sync."
        ]
        for result in self.moved:
            lines += [
                "",
                f"  ~ {result.space}/{result.title}",
                f"      {result.reason}",
                f"      repo: {result.path}",
                f"      reconcile: {result.reconcile}",
            ]
            if result.url:
                lines.append(f"      {result.url}")

        if self.absent:
            lines += ["", f"Declared but not published yet ({len(self.absent)}):"]
            lines += [f"  {r.path} -> {r.space}/{r.title}" for r in self.absent]

        if self.undeclared:
            lines += [
                "",
                self.undeclared_note
                or f"{self.undeclared} document(s) declare no `confluence:` block and were "
                "not looked up.",
            ]

        if not self.moved:
            lines += ["", "Nothing to reconcile."]
        return "\n".join(lines)


def drift_documents(publisher: Publisher, documents, *, only: str = "") -> DriftReport:
    """Where every published page stands against the tree that owns it.

    The same rules `publish_documents` applies, asked as a question: a page
    this tool wrote last, or whose version `pull` recorded, or that says
    what the repository says, is in sync; anything else has moved. Reads
    pages and writes nothing.
    """
    report = DriftReport()

    for doc in documents:
        if only and only not in doc.relative_path:
            continue
        location = publisher.location(doc)
        if not location:
            report.undeclared += 1
            continue
        title = publisher.title(doc)

        live = publisher.fetch_for(doc)
        if live is None:
            report.results.append(
                DriftResult(
                    path=doc.relative_path,
                    space=location,
                    title=title,
                    tier=doc.tier,
                    state=ABSENT,
                    reconcile=publisher.reconcile_command(doc),
                )
            )
            continue

        reason = moved_reason(publisher, doc, live)
        if reason and publisher.same_content(doc, live):
            # The page says what the repository says. Whoever wrote its last
            # version, there is nothing here to bring back.
            reason = ""
        report.results.append(
            DriftResult(
                path=doc.relative_path,
                space=location,
                title=title,
                tier=doc.tier,
                state=MOVED if reason else IN_SYNC,
                reason=reason,
                url=live.url,
                reconcile=publisher.reconcile_command(doc),
            )
        )

    report.undeclared_note = publisher.undeclared_note(report.undeclared, "were not looked up")
    return report


# --------------------------------------------------------------------------
# pull
# --------------------------------------------------------------------------

WRITTEN = "written"
UNCHANGED = "unchanged"
REFUSED = "refused"
#: The reason `pull` gives a page the converter can't take for its depth
#: (#252): markdownify recurses once per nested element.
TOO_DEEP = "page is nested too deeply to convert"
#: Each kind of refusal `pull` makes, and so each kind the report advises on.
REFUSAL_KINDS = ("macros", "not-found", "too-deep", "bound-twice", "slug-taken")
#: Said beside a page that no file is bound to, so it was placed by its
#: title (#387): the one case where `pull` chose the file itself.
PLACED_BY_TITLE = "no file is bound to this page, so it is placed by its title"


@dataclass
class PullResult:
    title: str
    path: str
    action: str
    reason: str = ""
    #: Which advice the report gives a refusal: one of `REFUSAL_KINDS`.
    kind: str = ""
    #: Said beside a written page, e.g. `PLACED_BY_TITLE`.
    note: str = ""

    def __post_init__(self) -> None:
        # A refusal of no known kind would get no advice at all, and look
        # complete doing it, so it fails here rather than in the report.
        if self.action == REFUSED and self.kind not in REFUSAL_KINDS:
            raise ValueError(f"refusal of unknown kind {self.kind!r}: {self.title}")


@dataclass
class PullReport:
    results: list[PullResult] = field(default_factory=list)
    dry_run: bool = True

    def _of(self, action: str) -> list[PullResult]:
        return [r for r in self.results if r.action == action]

    @property
    def refused(self) -> list[PullResult]:
        return self._of(REFUSED)

    def format_report(self) -> str:
        written = self._of(WRITTEN)
        unchanged = self._of(UNCHANGED)
        verb = "Would write" if self.dry_run else "Wrote"
        lines = [f"{verb} {len(written)} file(s); {len(unchanged)} already matched the tree."]
        lines += [f"  {r.title} -> {r.path}" + (f" ({r.note})" if r.note else "") for r in written]

        if self.refused:
            lines += ["", f"Refused {len(self.refused)}:"]
            lines += [f"  {r.title}: {r.reason}" for r in self.refused]
            if any(r.kind == "macros" for r in self.refused):
                lines += [
                    "",
                    "These pages read fine but would be damaged by a publish, so they are "
                    "not brought into the tree. Rewrite them in Confluence without those "
                    "macros, or keep them wiki-only.",
                ]
            if any(r.kind == "too-deep" for r in self.refused):
                lines += [
                    "",
                    "Pages nested too deeply to convert are not brought into the tree. "
                    "Flatten their nesting in Confluence, or keep them wiki-only.",
                ]
            if any(r.kind == "not-found" for r in self.refused):
                lines += [
                    "",
                    "Check the page title and space.",
                ]
            if any(r.kind == "bound-twice" for r in self.refused):
                lines += [
                    "",
                    "Several files claim one page, so pull can't tell which to update. "
                    "Remove the binding from all but one (`check` reports the same pair).",
                ]
            if any(r.kind == "slug-taken" for r in self.refused):
                lines += [
                    "",
                    "The file this page would be placed in belongs to another page. Bind a "
                    "file to this page in its frontmatter, and pull writes into that one.",
                ]

        if self.dry_run and written:
            lines += ["", "Nothing was written. Pass --apply to write these files."]
        return "\n".join(lines)


def pull_documents(
    publisher: Publisher,
    pages: list[tuple[str, str, str]],
    *,
    root: Path,
    target_path,
    dry_run: bool = True,
    allow_unsupported: bool = False,
) -> PullReport:
    """Fetch `(location, title, tier)` triples into the tree as markdown.

    Takes an explicit list rather than discovering pages itself, so the
    caller decides what is in scope. `target_path(root, tier=, slug=)` says
    where a pulled page lands when no file is bound to it; it is passed in
    because the layout of the tree is the tree's business, not the store's.

    **A page goes into the file bound to it** (#387): the one whose
    frontmatter names it as its target in this store, whatever that file
    is called. Placing it by its title instead put a second copy beside a
    differently named original, which `check` then rejected. Only a page
    no file is bound to is placed by title, and the report says so.

    **Frontmatter is merged, not replaced.** The binding (page id, version)
    is updated and every other key the file carries is kept: `topic`,
    `owner`, `generated_by`, its own `title` and `tier`. Replacing it
    dropped them on the first pull.
    """
    from policyforge.content.tree import load_content_tree, render_document
    from policyforge.export.confluence_importer import PageTooDeepError
    from policyforge.textfile import normalise_newlines, write_text_lf
    from policyforge.zardoz.corpus import slugify

    report = PullReport(dry_run=dry_run)
    documents, _problems = load_content_tree(root)
    by_path = {doc.path.resolve(): doc for doc in documents}
    # The tree above is read once, so a file this run writes is not in it.
    # Each destination the run writes or plans is claimed here for its page,
    # so a later page in the same run can't take the file over either.
    claimed: dict[Path, tuple[str, str]] = {}

    for location, title, tier in pages:
        try:
            live = publisher.fetch(location, title)
        except LookupError as exc:
            report.results.append(
                PullResult(title=title, path="", action=REFUSED, reason=str(exc), kind="not-found")
            )
            continue

        if live.unsupported and not allow_unsupported:
            report.results.append(
                PullResult(
                    title=title,
                    path="",
                    action=REFUSED,
                    reason=(
                        "uses macros that would not survive a publish "
                        f"({', '.join(live.unsupported)})"
                    ),
                    kind="macros",
                )
            )
            continue

        try:
            body = publisher.as_markdown(live)
        except PageTooDeepError:
            report.results.append(
                PullResult(
                    title=title,
                    path="",
                    action=REFUSED,
                    reason=TOO_DEEP,
                    kind="too-deep",
                )
            )
            continue
        bound = [doc for doc in documents if _names_page(publisher, doc, location, title)]
        if len(bound) > 1:
            report.results.append(
                PullResult(
                    title=title,
                    path="",
                    action=REFUSED,
                    reason="bound to it: " + ", ".join(doc.relative_path for doc in bound),
                    kind="bound-twice",
                )
            )
            continue

        note = ""
        if bound:
            destination = bound[0].path
            existing = bound[0]
        else:
            slug = slugify(title) or slugify(live.page_id)
            destination = target_path(root, tier=tier, slug=slug)
            existing = by_path.get(destination.resolve())
            # Placing by title must not take over a file another page owns:
            # merging this page's binding into it would rebind that file and
            # replace the other page's text (two pages with one title in two
            # spaces share a slug). The owner is the page the file is bound
            # to on disk, or the page this run has already put there.
            owner, when = claimed.get(destination.resolve()), " earlier in this run"
            if owner is None and existing is not None and publisher.location(existing):
                owner, when = (publisher.location(existing), publisher.title(existing)), ""
            if owner is not None and owner != (location, title):
                report.results.append(
                    PullResult(
                        title=title,
                        path="",
                        action=REFUSED,
                        reason=(
                            f"{destination.relative_to(root).as_posix()} is bound to another "
                            f"page ({owner[0]}: {owner[1]}){when}"
                        ),
                        kind="slug-taken",
                    )
                )
                continue
            note = PLACED_BY_TITLE
        claimed[destination.resolve()] = (location, title)

        # The binding travels too: it is how `publish` knows a person has
        # already pulled and reviewed this edit, and so may overwrite the
        # page without destroying anything unseen.
        rendered = render_document(
            _pulled_frontmatter(
                existing, title=title, tier=tier, binding=publisher.binding(location, title, live)
            ),
            body,
        )
        relative = destination.relative_to(root).as_posix()

        # Compared as it will be written. `read_text` folds CRLF on the way
        # in and `write_text_lf` folds it on the way out, so a page whose
        # body carried one read as changed on every pull until the rendered
        # side was folded too.
        rendered = normalise_newlines(rendered)
        if destination.exists() and destination.read_text(encoding="utf-8") == rendered:
            report.results.append(PullResult(title=title, path=relative, action=UNCHANGED))
            continue

        if not dry_run:
            destination.parent.mkdir(parents=True, exist_ok=True)
            # A pulled page lands in a tracked tree, where the gate runs
            # mdformat over it: LF, whatever platform pulled it.
            write_text_lf(destination, rendered)
        report.results.append(PullResult(title=title, path=relative, action=WRITTEN, note=note))

    return report


def _names_page(publisher: Publisher, doc, location: str, title: str) -> bool:
    """Whether `doc` is bound, in this publisher's store, to the page at
    `location` titled `title`.

    The same reading `publish` makes of the file, so a page is pulled into
    exactly the file that would be published over it. A document that
    declares no target in this store is bound to nothing. An empty
    `location` is a wiki page whose repository comes from configuration,
    which every wiki-bound file shares, so the title alone decides.
    """
    doc_location = publisher.location(doc)
    if not doc_location:
        return False
    return publisher.title(doc) == title and (not location or doc_location == location)


def _merge(into: dict, new: dict) -> dict:
    """`into` updated from `new`, nested mappings merged rather than replaced."""
    merged = dict(into)
    for key, value in new.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _pulled_frontmatter(existing, *, title: str, tier: str, binding: dict) -> dict:
    """The frontmatter a pulled page is written with.

    A new file gets the title, the tier and the binding. A file already in
    the tree keeps all of its own keys, and only the binding is updated. Its
    title is its own, from its frontmatter or its heading, and may differ
    from the page's on purpose. Its tier is the one its frontmatter or its
    directory gives, which a pull's tier (the registry's, or `--tier`'s
    default) must not overrule; a file with neither gets the pull's.
    """
    if existing is None:
        return {"title": title, "tier": tier, **binding}

    metadata = dict(existing.metadata)
    if not existing.tier:
        metadata["tier"] = tier
    # A file that spells its binding under `targets:` is updated there, so
    # it doesn't end up with two blocks for one store.
    for key, value in binding.items():
        targets = metadata.get("targets")
        if key not in metadata and isinstance(targets, dict) and isinstance(targets.get(key), dict):
            metadata = _merge(metadata, {"targets": {key: value}})
        else:
            metadata = _merge(metadata, {key: value})
    return metadata


# --------------------------------------------------------------------------
# Confluence
# --------------------------------------------------------------------------


def _storage_key(storage: str) -> str:
    """Storage markup with the whitespace between tags collapsed, and the
    bookkeeping a server adds to each macro taken off.

    Confluence may reflow storage format when it saves a page, and a
    difference in the gaps between elements is not an edit anybody made.
    It also stamps every macro with `ac:schema-version` and an `ac:macro-id`
    (the page shape b5 found on PR 431), which the exporter never writes, so
    a page with a macro never read as the same and was written again on every
    run (449). Nothing else is normalized: any other difference counts as an
    edit, which is the direction to be wrong in.
    """
    import re

    # Only inside a macro's opening tag, so text that spells one stays.
    storage = re.sub(
        r"<ac:structured-macro\b[^>]*>",
        lambda tag: re.sub(r'\s+ac:(?:schema-version|macro-id)="[^"]*"', "", tag.group(0)),
        storage,
    )
    return re.sub(r">\s+<", "><", storage.strip())


class ConfluencePublisher(Publisher):
    """Confluence Cloud, Server or Data Center over its REST API.

    Wraps `confluence_importer`, `confluence_exporter` and
    `confluence_search` as they are; every function is resolved at call
    time through its module, so a test that fakes the network there fakes
    it here too.
    """

    kind = CONFLUENCE

    def __init__(self, *, host: str):
        self.host = host

    def location(self, doc) -> str:
        return doc.space

    def title(self, doc) -> str:
        return doc.page_title

    def recorded_version(self, doc) -> str | None:
        recorded = doc.confluence.get("version")
        try:
            return str(int(recorded)) if recorded is not None else None
        except (TypeError, ValueError):
            return None

    def fetch(self, location: str, title: str) -> LivePage:
        from policyforge.export import confluence_importer

        return self.live_page(
            confluence_importer.fetch_confluence_page(space=location, title=title, host=self.host)
        )

    @staticmethod
    def live_page(page) -> LivePage:
        """A `ConfluencePage` in the publisher's terms."""
        from policyforge.edit.apply import detect_unsupported_macros
        from policyforge.export.confluence_exporter import LIVE_EDIT_MARKER, PUBLISH_MARKER

        # `getattr`: a `ConfluencePage` always carries the message, but
        # `pull` never needed it, and the fakes its tests hand in do not.
        message = getattr(page, "version_message", "") or ""
        return LivePage(
            version=str(page.version),
            written_by_tool=PUBLISH_MARKER in message,
            live_edit=LIVE_EDIT_MARKER in message,
            url=page.webui_url,
            body=page.storage_body or "",
            page_id=page.id,
            unsupported=tuple(detect_unsupported_macros(page.storage_body)),
        )

    def same_content(self, doc, live: LivePage) -> bool:
        """Compared in storage format, the way a page comes back from
        Confluence, so a page published before the stamp existed is adopted
        rather than reported as moved on the first run after upgrading."""
        from policyforge.export.confluence_exporter import markdown_to_confluence

        return _storage_key(markdown_to_confluence(doc.body)) == _storage_key(live.body)

    def as_markdown(self, live: LivePage) -> str:
        from policyforge.export import confluence_search
        from policyforge.export.confluence_importer import convert_page, extract_user_ids

        names = confluence_search.fetch_user_names(extract_user_ids(live.body), host=self.host)
        # A LivePage has no title; pull's refusal names the page by its own
        # title and the TOO_DEEP reason, so the id only has to identify it.
        return convert_page(live.body, title=live.page_id or live.url, user_names=names)

    def binding(self, location: str, title: str, live: LivePage) -> dict:
        return {
            "confluence": {
                "space": location,
                "title": title,
                "page_id": live.page_id,
                "version": int(live.version),
            }
        }

    def write(self, doc) -> str:
        from policyforge.export import confluence_exporter

        return confluence_exporter.export_to_confluence(
            doc.body, space=doc.space, title=doc.page_title, host=self.host
        )

    def reconcile_command(self, doc) -> str:
        # **Quoted with `shlex.quote`, because this line is printed for a
        # person to copy.** A document title is user-authored prose: the
        # title `Vendor "Bring Your Own" Policy` inside a hand-written
        # `"..."` produces `--title "Vendor Bring"`, which is a valid
        # command naming the wrong document, silently. #187.
        return (
            f"policyforge pull --space {shlex.quote(doc.space)} "
            f"--title {shlex.quote(doc.page_title)} "
            f"--tier {shlex.quote(doc.tier or 'standard')} --apply"
        )
