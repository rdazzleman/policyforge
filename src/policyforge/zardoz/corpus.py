"""A local snapshot of the documents Zardoz answers from.

Documents reach the corpus from two places, and the difference is about
authority rather than convenience:

* **markdown** — files in the content tree. In a repo-backed setup these are
  the source of truth: they are what review happens on, what git history
  records, and what gets published. Reading them needs no network and no
  credentials at all, which means the whole answering path can be developed,
  tested and demonstrated without an Atlassian account.
* **confluence** — pages fetched from the wiki. In a repo-backed setup this
  is the *published output*, useful for finding what has drifted from the
  tree; where the wiki is still the system of record it is the only source
  there is. Either way it costs a round trip per page.

Why a snapshot rather than reading live on every question: a Confluence round
trip is 300-800ms, answering one question well wants several, the API is
rate-limited per token, and a conversation is a burst rather than a trickle.
A local corpus also means retrieval can be developed against fixtures that
answer identically every time, which is what keeps ranking work honest.

Orthogonally to where a document came from, the corpus carries two
confidence levels, and the distinction is load-bearing:

* **trusted** — the document knows who is accountable for it, because the
  topic registry declares it or its frontmatter says so. An answer drawn
  from it can name an owner and say whether a threshold belongs there.
* **supporting** — real content that nobody has claimed: a page from the
  configured extra space, or a file in the tree with no topic and no owner.
  Answers may draw on it and must say they did.

Sync is deliberately forgiving of individual failures and unforgiving of
silent ones. A registry page whose title no longer matches is recorded as a
skip with its reason and the run continues, because one renamed page should
not cost you the other nineteen topics. But a sync that resolved *nothing*
will not overwrite a corpus that has documents in it: losing a working
snapshot to a typo'd space key is a failure you would discover by getting
worse answers, which is the worst way to discover anything.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path

from policyforge.textfile import write_text_lf
from policyforge.topics.registry import Topic

DEFAULT_CORPUS_DIR = Path("output/.zardoz")
DEFAULT_CONTENT_DIR = Path("output")

TRUSTED = "trusted"
SUPPORTING = "supporting"

MARKDOWN = "markdown"
CONFLUENCE = "confluence"

#: Bumped whenever the manifest gains a field an older reader would need in
#: order to make sense of it. A corpus written by a newer PolicyForge is
#: refused with an instruction to re-sync, rather than crashed into.
CORPUS_SCHEMA = 1

#: How old a snapshot gets before answering from it deserves a warning.
#: Published documents change; a month-old snapshot quoted with confidence
#: is precisely the failure this package is built to avoid.
STALE_AFTER_DAYS = 14


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def _digest(*parts: str) -> str:
    """A short, stable hash of a document's identity.

    Stability matters more than brevity: `doc_id` is the filename on disk,
    and an id that changed between syncs would leave the previous file
    behind as an orphan for the stale-pruning pass to delete and rewrite.
    """
    joined = "\x00".join(parts)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:8]


def _doc_id(*, prefix: str, name: str, identity: str) -> str:
    """Build a filesystem-safe id, falling back to a hash when it must.

    `slugify` keeps only ASCII alphanumerics, so a title written in
    Japanese, Russian, or any other non-Latin script slugifies to nothing at
    all. Left alone, every such page in one space collapses onto a single id
    and they overwrite each other's bodies on disk — the corpus then serves
    one document's text under another's title, which is the exact confident
    wrongness the whole package exists to prevent. So an empty slug becomes
    a hash of the identity instead: opaque to read, but distinct, stable,
    and never someone else's content.
    """
    stem = slugify(name) or f"doc-{_digest(identity)}"
    head = slugify(prefix)
    return f"{head}-{stem}" if head else stem


def _ensure_unique_ids(documents: list[CorpusDocument]) -> None:
    """Give every document its own id, in place.

    `_doc_id` removes the common collision, but two genuinely different
    titles can still slugify the same way ("A/B Testing" and "A B Testing").
    Whoever arrives second takes a suffixed id rather than silently
    inheriting the first one's file.
    """
    seen: set[str] = set()
    for doc in documents:
        candidate = doc.doc_id
        if candidate in seen:
            candidate = f"{doc.doc_id}-{_digest(doc.source, doc.space, doc.title, doc.path)}"
        while candidate in seen:  # pragma: no cover - a hash collision
            candidate = f"{candidate}-x"
        seen.add(candidate)
        doc.doc_id = candidate


@dataclass
class CorpusDocument:
    """One document, as Zardoz holds it.

    `body` is markdown and lives in its own file rather than in the
    manifest: it is the only large field, and keeping the manifest small
    means listing what is synced does not mean parsing every document.
    """

    doc_id: str
    title: str
    space: str
    confidence: str
    #: Where this came from — `markdown` or `confluence`. Answers cite the
    #: two differently: a repo path is something the reader can open and
    #: change, a wiki URL is something they can only read.
    source: str = CONFLUENCE
    #: Repo-relative path, for markdown documents.
    path: str = ""
    #: The Confluence page a markdown document publishes to, when its
    #: frontmatter binds one. A repo path and a page title are different
    #: strings — `standards/ac.md` can publish to "Acme Access Control
    #: Standard" — so the binding has to be carried rather than inferred,
    #: or a file and its own published copy both end up in the corpus.
    published_title: str = ""
    page_id: str = ""
    version: int = 0
    webui_url: str = ""
    tier: str = ""
    topic: str = ""
    owner: str = ""
    labels: list[str] = field(default_factory=list)
    ancestors: list[str] = field(default_factory=list)
    #: What this document links to. The document graph, which a policy set
    #: generates naturally — a Standard and its Procedure cite each other.
    #: Confluence pages carry page titles here; markdown files carry link
    #: targets as written.
    references: list[str] = field(default_factory=list)
    #: Mentions that could not be resolved to a display name. Counted rather
    #: than ignored, because the field these usually appear in is "Owner".
    unresolved_users: int = 0
    #: Macros that survive reading but would be damaged by a write. Recorded
    #: so Zardoz can say "I can answer from this page but not safely change
    #: it" instead of proposing an edit that would flatten them.
    unsupported_macros: list[str] = field(default_factory=list)
    synced_at: str = ""
    body: str = ""

    @property
    def label(self) -> str:
        return f"{self.title} ({self.tier})" if self.tier else self.title

    @property
    def is_trusted(self) -> bool:
        return self.confidence == TRUSTED

    @property
    def location(self) -> str:
        """Where to send a reader to see this for themselves.

        One accessor over both sources, so that citation code never has to
        ask which kind of document it is holding.
        """
        return self.path if self.source == MARKDOWN else self.webui_url

    def reader_directed(self) -> list:
        """Stretches of this document that address their reader, not the org.

        Computed rather than stored. `body` is deliberately the only large
        field and the manifest is deliberately small, so a cached list of
        findings would be a third copy of text this can derive in a
        millisecond — and one that goes stale the moment the page is edited.

        See `zardoz/injection.py` for what counts and, more importantly, for
        what does not: a policy set is imperative from end to end, so
        commanding language is the content rather than the signal.
        """
        from .injection import scan_document

        return scan_document(self.title, self.body)

    @property
    def is_editable(self) -> bool:
        """Whether a change to this document can be made safely.

        A markdown file always can be — it is a file, and review happens on
        the diff. A Confluence page carrying macros this project cannot
        round-trip cannot, and saying so beats proposing an edit that would
        silently flatten them.
        """
        return self.source == MARKDOWN or not self.unsupported_macros


@dataclass
class Corpus:
    """Everything synced, with the lookups answering needs."""

    documents: list[CorpusDocument] = field(default_factory=list)
    synced_at: str = ""
    host: str = ""

    def __len__(self) -> int:
        return len(self.documents)

    @property
    def trusted(self) -> list[CorpusDocument]:
        return [doc for doc in self.documents if doc.is_trusted]

    @property
    def supporting(self) -> list[CorpusDocument]:
        return [doc for doc in self.documents if not doc.is_trusted]

    @property
    def from_markdown(self) -> list[CorpusDocument]:
        return [doc for doc in self.documents if doc.source == MARKDOWN]

    @property
    def from_confluence(self) -> list[CorpusDocument]:
        return [doc for doc in self.documents if doc.source == CONFLUENCE]

    def by_topic(self, name: str) -> list[CorpusDocument]:
        return [doc for doc in self.documents if doc.topic.lower() == name.lower()]

    def get(self, doc_id: str) -> CorpusDocument | None:
        return next((doc for doc in self.documents if doc.doc_id == doc_id), None)

    @property
    def age_days(self) -> float | None:
        """How long ago this was synced, or None if that can't be told."""
        if not self.synced_at:
            return None
        try:
            when = datetime.fromisoformat(self.synced_at)
        except ValueError:
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - when).total_seconds() / 86400

    @property
    def is_stale(self) -> bool:
        age = self.age_days
        return age is not None and age >= STALE_AFTER_DAYS


@dataclass
class SyncReport:
    """What one sync did, and what it could not do."""

    synced: list[CorpusDocument] = field(default_factory=list)
    #: (what was being fetched, why it failed) — never raised, so that one
    #: bad page cannot cost the rest of the run.
    skipped: list[tuple[str, str]] = field(default_factory=list)
    corpus_dir: Path = DEFAULT_CORPUS_DIR
    #: Set when the run resolved nothing and an existing corpus was left
    #: alone rather than replaced with an empty one.
    refused_empty: bool = False

    @property
    def unresolved_users(self) -> int:
        return sum(doc.unresolved_users for doc in self.synced)

    def format_report(self) -> str:
        if self.refused_empty:
            return "\n".join(
                [
                    "Synced 0 documents, so the existing corpus was left in place.",
                    "",
                    *(f"  {what}: {why}" for what, why in self.skipped),
                    "",
                    "Nothing resolved, which usually means a wrong space key, a content "
                    "directory that isn't there, or pages that have been renamed. The "
                    "previous snapshot is untouched and Zardoz will keep answering from "
                    "it — fix the cause and sync again, or pass --allow-empty to clear "
                    "the corpus on purpose.",
                ]
            )

        trusted = [doc for doc in self.synced if doc.is_trusted]
        supporting = [doc for doc in self.synced if not doc.is_trusted]
        local = [doc for doc in self.synced if doc.source == MARKDOWN]
        remote = [doc for doc in self.synced if doc.source == CONFLUENCE]
        lines = [
            f"Synced {len(self.synced)} document(s) to {self.corpus_dir}",
            f"  {len(trusted)} trusted (owner known)  {len(supporting)} supporting (unowned)",
            f"  {len(local)} from markdown  {len(remote)} from Confluence",
        ]

        if self.skipped:
            lines += ["", f"Skipped {len(self.skipped)}:"]
            lines += [f"  {what}: {why}" for what, why in self.skipped]

        if self.unresolved_users:
            lines += [
                "",
                f"{self.unresolved_users} user mention(s) could not be resolved to a name "
                "and read as @unresolved-user.",
                "  These are usually the Owner field. Check the token can read user "
                "profiles, or write the owner as plain text on the page.",
            ]

        brittle = [doc for doc in self.synced if doc.unsupported_macros]
        if brittle:
            lines += [
                "",
                f"{len(brittle)} page(s) use macros that read fine but cannot be edited safely:",
            ]
            lines += [f"  {doc.title}: {', '.join(doc.unsupported_macros)}" for doc in brittle[:10]]
            if len(brittle) > 10:
                lines.append(f"  ... and {len(brittle) - 10} more")

        # Reported here rather than on the answer, and that is the whole
        # design. A warning at sync time reaches somebody who can still open
        # the page and look; the same warning stapled to an answer arrives
        # after the fact and teaches its reader to click past warnings.
        directed = [(doc, doc.reader_directed()) for doc in self.synced]
        directed = [(doc, findings) for doc, findings in directed if findings]
        if directed:
            count = sum(len(findings) for _, findings in directed)
            lines += [
                "",
                f"{count} passage(s) in {len(directed)} document(s) read as instructions to "
                "whoever is answering, rather than as policy:",
            ]
            for doc, findings in directed[:10]:
                where = doc.location or doc.title
                lines.append(f"  {doc.title} ({where})")
                for finding in findings[:3]:
                    lines.append(f"    {finding}")
                if len(findings) > 3:
                    lines.append(f"    ... and {len(findings) - 3} more in this document")
            if len(directed) > 10:
                lines.append(f"  ... and {len(directed) - 10} more document(s)")
            lines += [
                "  Zardoz fences passage text so it cannot be read as instruction, so this "
                "is not an outage.",
                "  It is worth looking at anyway: usually a page somebody pasted a chat "
                "transcript into, occasionally not.",
            ]

        return "\n".join(lines)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _UserNames:
    """Resolve account ids to display names once per sync, not once per page.

    Every page in a policy set tends to name the same handful of owners, and
    each unresolved id costs up to two HTTP requests. Looking them up per
    page turned a 60-page sync into 60 lookups of the same two people.
    Failures are remembered too: an id that could not be resolved will not
    resolve on the next page either, and retrying it 59 more times only
    makes the sync slower.
    """

    def __init__(self, host: str) -> None:
        self._host = host
        self._names: dict[str, str] = {}
        self._attempted: set[str] = set()

    def resolve(self, account_ids: set[str]) -> dict[str, str]:
        from policyforge.export.confluence_search import fetch_user_names

        missing = account_ids - self._attempted
        if missing:
            self._names.update(fetch_user_names(missing, host=self._host))
            self._attempted.update(missing)
        return {key: self._names[key] for key in account_ids if key in self._names}


def _document_from_page(page, *, confidence: str, **known) -> CorpusDocument:
    """Convert a fetched Confluence page into a corpus document."""
    from policyforge.edit.apply import detect_unsupported_macros
    from policyforge.export.confluence_importer import (
        UNRESOLVED_USER,
        confluence_to_markdown,
        extract_references,
    )

    user_names = known.pop("user_names", {}) or {}
    space = known.get("space", "")
    body = confluence_to_markdown(page.storage_body, user_names=user_names)
    return CorpusDocument(
        doc_id=_doc_id(prefix=space, name=page.title, identity=f"{space}\x00{page.title}"),
        title=page.title,
        confidence=confidence,
        source=CONFLUENCE,
        page_id=page.id,
        version=page.version,
        webui_url=page.webui_url,
        labels=list(page.labels),
        ancestors=list(page.ancestors),
        references=extract_references(page.storage_body),
        unresolved_users=body.count(UNRESOLVED_USER),
        unsupported_macros=detect_unsupported_macros(page.storage_body),
        synced_at=_now(),
        body=body,
        **known,
    )


def _document_from_file(doc, *, topics: list[Topic]) -> CorpusDocument:
    """Convert a content-tree file into a corpus document.

    Ownership is resolved in the order the information is trustworthy:
    what the file declares in its frontmatter, then what the registry says
    about a topic whose name matches the file's slug — which is how
    `synthesize` names them, so an unannotated tree still resolves. A file
    that answers to neither is real content nobody has claimed, which is
    what `supporting` means.
    """
    topic, owner = doc.topic, doc.owner
    if not topic or not owner:
        match = next((t for t in topics if slugify(t.name) == slugify(doc.slug)), None)
        if match is None:
            match = next((t for t in topics if t.name.lower() == topic.lower() and topic), None)
        if match is not None:
            topic = topic or match.name
            owner = owner or match.owner

    return CorpusDocument(
        doc_id=_doc_id(prefix="", name=doc.relative_path[:-3], identity=doc.relative_path),
        title=doc.title,
        space=doc.space,
        confidence=TRUSTED if owner else SUPPORTING,
        source=MARKDOWN,
        path=doc.relative_path,
        published_title=doc.page_title,
        page_id=doc.page_id,
        tier=doc.tier,
        topic=topic,
        owner=owner,
        references=doc.references,
        synced_at=_now(),
        body=doc.body,
    )


def sync_markdown(
    content_dir: Path, topics: list[Topic], *, report: SyncReport
) -> list[CorpusDocument]:
    """Read the content tree into corpus documents, recording what failed."""
    from policyforge.content.tree import load_content_tree

    documents, problems = load_content_tree(content_dir)
    report.skipped.extend(problems)
    return [_document_from_file(doc, topics=topics) for doc in documents]


def sync_confluence(
    topics: list[Topic],
    *,
    host: str,
    supporting_space: str = "",
    max_results: int = 500,
    claimed_titles: set[str] | None = None,
    report: SyncReport,
) -> list[CorpusDocument]:
    """Pull every declared page, plus the supporting space.

    Trusted pages are fetched one at a time by exact title, because that is
    what the registry declares. The supporting space is discovered with one
    CQL query, because nobody has listed its contents.
    """
    from policyforge.export.confluence_importer import extract_user_ids, fetch_confluence_page
    from policyforge.export.confluence_search import SearchLimitExceeded, search_pages, space_cql

    names = _UserNames(host)
    documents: list[CorpusDocument] = []
    seen_titles: set[str] = set(claimed_titles or ())
    # (space, title) -> the topic that got there first. Two topics naming
    # the same page is not a duplicate fetch to be quietly deduped: it is
    # two teams claiming one document, which is the contested-ownership
    # problem `policyforge coverage` exists to surface. Answering from it
    # twice under two different owners would bury exactly that.
    claimed: dict[tuple[str, str], str] = {}

    for topic in topics:
        space = (topic.confluence or {}).get("space", "")
        for tier, title in topic.confluence_pages():
            if not space:
                report.skipped.append((title, "topic declares pages but no space"))
                continue

            key = (space.lower(), title.lower())
            if key in claimed:
                report.skipped.append(
                    (
                        f"{title} ({tier})",
                        f"already claimed by topic {claimed[key]!r}; one page cannot "
                        f"have two owners, so {topic.name!r} was not also given it",
                    )
                )
                continue
            claimed[key] = topic.name

            if title.lower() in seen_titles:
                report.skipped.append(
                    (f"{title} ({tier})", "already synced from the content tree, which wins")
                )
                continue

            try:
                page = fetch_confluence_page(space=space, title=title, host=host)
            except LookupError as exc:
                report.skipped.append((f"{title} ({tier})", str(exc)))
                continue

            documents.append(
                _document_from_page(
                    page,
                    confidence=TRUSTED,
                    space=space,
                    tier=tier,
                    topic=topic.name,
                    owner=topic.owner,
                    user_names=names.resolve(extract_user_ids(page.storage_body)),
                )
            )
            seen_titles.add(title.lower())

    if supporting_space:
        try:
            pages = search_pages(
                host=host,
                cql=space_cql(supporting_space),
                with_body=True,
                max_results=max_results,
            )
        except SearchLimitExceeded as exc:
            report.skipped.append((f"space {supporting_space}", str(exc)))
            pages = []

        for page in pages:
            # A supporting copy of a page something already claims would let
            # the same requirement be cited twice at two confidence levels.
            # The claimed one wins.
            if page.title.lower() in seen_titles:
                continue
            documents.append(
                _document_from_page(
                    page,
                    confidence=SUPPORTING,
                    space=supporting_space,
                    user_names=names.resolve(extract_user_ids(page.storage_body)),
                )
            )
            seen_titles.add(page.title.lower())

    return documents


def sync_corpus(
    topics: list[Topic],
    *,
    host: str = "",
    content_dir: Path | None = None,
    supporting_space: str = "",
    corpus_dir: Path = DEFAULT_CORPUS_DIR,
    max_results: int = 500,
    allow_empty: bool = False,
) -> SyncReport:
    """Build the snapshot from whichever sources are configured.

    The content tree is read first and wins any collision. Where a repo
    publishes to Confluence, the file is the source of truth and the page is
    a copy of it; holding both would cite one requirement twice and invite an
    answer that quotes the stale half.
    """
    if not content_dir and not host:
        raise ValueError(
            "Nothing to sync from: give a content directory, a Confluence host, or both."
        )

    report = SyncReport(corpus_dir=corpus_dir)
    documents: list[CorpusDocument] = []

    if content_dir:
        documents.extend(sync_markdown(content_dir, topics, report=report))

    if host:
        documents.extend(
            sync_confluence(
                topics,
                host=host,
                supporting_space=supporting_space,
                max_results=max_results,
                claimed_titles={(doc.published_title or doc.title).lower() for doc in documents},
                report=report,
            )
        )

    _ensure_unique_ids(documents)
    report.synced = documents

    if not documents and not allow_empty and _corpus_size(corpus_dir):
        report.refused_empty = True
        return report

    write_corpus(documents, corpus_dir=corpus_dir, host=host)
    return report


def _corpus_size(corpus_dir: Path) -> int:
    """How many documents the corpus on disk already holds.

    Unreadable counts as empty: the point of asking is to avoid destroying
    something valuable, and a manifest that cannot be parsed is not that.
    """
    manifest_path = corpus_dir / "manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    documents = manifest.get("documents")
    return len(documents) if isinstance(documents, list) else 0


def write_corpus(
    documents: list[CorpusDocument], *, corpus_dir: Path = DEFAULT_CORPUS_DIR, host: str = ""
) -> None:
    """Write the manifest and one markdown file per document."""
    docs_dir = corpus_dir / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    # Drop files for documents that are no longer in the registry, the
    # content tree or the supporting space, so a corpus cannot accumulate
    # documents that were deliberately removed and then answer from them.
    current = {f"{doc.doc_id}.md" for doc in documents}
    for stale in docs_dir.glob("*.md"):
        if stale.name not in current:
            stale.unlink()

    records = []
    for doc in documents:
        # LF like every other markdown this tool writes; a snapshot that
        # differs from its source by line endings alone is a false change.
        write_text_lf(docs_dir / f"{doc.doc_id}.md", doc.body)
        record = asdict(doc)
        record.pop("body")
        records.append(record)

    manifest = {
        "schema": CORPUS_SCHEMA,
        "synced_at": _now(),
        "host": host,
        "documents": records,
    }
    write_text_lf(corpus_dir / "manifest.json", json.dumps(manifest, indent=2))


def load_corpus(corpus_dir: Path = DEFAULT_CORPUS_DIR) -> Corpus:
    """Read a synced corpus back off disk.

    Raises FileNotFoundError when nothing has been synced — the shell turns
    that into "run sync first" rather than answering from an empty corpus,
    which would look like the documents simply do not say anything.
    """
    manifest_path = corpus_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(
            f"No corpus at {corpus_dir}. Run `policyforge zardoz sync` to build one."
        )

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    schema = manifest.get("schema", 0)
    if schema > CORPUS_SCHEMA:
        raise ValueError(
            f"The corpus at {corpus_dir} was written by a newer PolicyForge "
            f"(manifest schema {schema}, this version reads {CORPUS_SCHEMA}). "
            "Run `policyforge zardoz sync` to rebuild it."
        )

    # Unknown keys are dropped rather than passed through to the constructor.
    # The manifest gains fields as the milestones land, and a corpus synced
    # before an upgrade should make the shell say "re-sync", not raise a
    # TypeError out of a dataclass on launch.
    known = {f.name for f in fields(CorpusDocument)}
    documents = []
    for record in manifest.get("documents", []):
        fields_present = {key: value for key, value in record.items() if key in known}
        body_path = corpus_dir / "docs" / f"{fields_present.get('doc_id', '')}.md"
        try:
            documents.append(
                CorpusDocument(
                    **fields_present,
                    body=body_path.read_text(encoding="utf-8") if body_path.exists() else "",
                )
            )
        except TypeError as exc:
            raise ValueError(
                f"The corpus at {corpus_dir} is missing fields this version needs ({exc}). "
                "Run `policyforge zardoz sync` to rebuild it."
            ) from exc

    return Corpus(
        documents=documents,
        synced_at=manifest.get("synced_at", ""),
        host=manifest.get("host", ""),
    )
