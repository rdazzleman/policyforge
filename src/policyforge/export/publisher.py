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
    by = (
        f"last written by {live.author}"
        if live.author
        else "last written by someone other than this tool"
    )
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
        lines.append(
            f"{verb} {len(self.published)} page(s): "
            f"{len(self._of(CREATED))} new, {len(self._of(UPDATED))} updated"
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


@dataclass
class PullResult:
    title: str
    path: str
    action: str
    reason: str = ""


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
        lines += [f"  {r.title} -> {r.path}" for r in written]

        if self.refused:
            lines += ["", f"Refused {len(self.refused)}:"]
            lines += [f"  {r.title}: {r.reason}" for r in self.refused]
            lines += [
                "",
                "These pages read fine but would be damaged by a publish, so they are "
                "not brought into the tree. Rewrite them in Confluence without those "
                "macros, or keep them wiki-only.",
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
    where a pulled page lands; it is passed in because the layout of the
    tree is the tree's business, not the store's.
    """
    from policyforge.content.tree import render_document
    from policyforge.textfile import normalise_newlines, write_text_lf
    from policyforge.zardoz.corpus import slugify

    report = PullReport(dry_run=dry_run)

    for location, title, tier in pages:
        try:
            live = publisher.fetch(location, title)
        except LookupError as exc:
            report.results.append(PullResult(title=title, path="", action=REFUSED, reason=str(exc)))
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
                )
            )
            continue

        body = publisher.as_markdown(live)
        slug = slugify(title) or slugify(live.page_id)
        destination = target_path(root, tier=tier, slug=slug)

        # The binding travels too: it is how `publish` knows a person has
        # already pulled and reviewed this edit, and so may overwrite the
        # page without destroying anything unseen.
        rendered = render_document(
            {"title": title, "tier": tier, **publisher.binding(location, title, live)},
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
        report.results.append(PullResult(title=title, path=relative, action=WRITTEN))

    return report


# --------------------------------------------------------------------------
# Confluence
# --------------------------------------------------------------------------


def _storage_key(storage: str) -> str:
    """Storage markup with the whitespace between tags collapsed.

    Confluence may reflow storage format when it saves a page, and a
    difference in the gaps between elements is not an edit anybody made.
    Nothing else is normalized: any other difference counts as an edit,
    which is the direction to be wrong in.
    """
    import re

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
        from policyforge.export.confluence_exporter import PUBLISH_MARKER

        return LivePage(
            version=str(page.version),
            # `getattr`: a `ConfluencePage` always carries the message, but
            # `pull` never needed it, and the fakes its tests hand in do not.
            written_by_tool=PUBLISH_MARKER in (getattr(page, "version_message", "") or ""),
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
        from policyforge.export.confluence_importer import confluence_to_markdown, extract_user_ids

        names = confluence_search.fetch_user_names(extract_user_ids(live.body), host=self.host)
        return confluence_to_markdown(live.body, user_names=names)

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
