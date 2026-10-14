"""Which model wrote this document, recorded in the document.

`generate` already builds the right stamp — the models that answered, how
many calls, the prompt hashes, the provider and its class, the cost, the
content class — and writes it to `output/.history`, which is gitignored.
That is the wrong place for it in a repo-backed setup, for two reasons.

The first is that the local store duplicates git. A tree under version
control already records what changed and when; what it does not record is
*who wrote it*, and that is the one thing the stamp knows.

The second is the question the stamp exists to answer. When a model is
found to systematically weaken cited requirements — turning "shall" into
"should consider", which `content/deontic.py` exists to catch — the next
question is which documents it touched. In a gitignored directory on one
person's laptop that question is answerable by that person, for as long as
they keep the directory. In frontmatter it is answerable by anyone with a
checkout, with `git log`, and about a document that was published two years
ago.

So the stamp travels in the document, as a `generated_by:` block.

**It does not survive a round trip through Confluence.** A document that was
generated, published, and later pulled back comes home as storage format
converted to markdown, and the frontmatter this wrote is not in the page
body. `import-confluence` writes an `imported_from` record in its place and
carries the predecessor's content class forward (see below, #197), but it
never restores `models`: which model wrote an edited page is not known.
That is a real limit and it is why absence is never treated as
suspicious: an unstamped document may be hand-written, may predate this, or
may have been pulled. What a stamp asserts is positive only — this model
wrote this text — and nothing is inferred from its absence.
"""

from __future__ import annotations

from dataclasses import dataclass

#: The frontmatter key. One block rather than scattered keys so a reader can
#: see at a glance which part of the frontmatter a person chose and which
#: part a tool wrote.
GENERATED_BY = "generated_by"


def stamp_document(text: str, stamp: dict) -> str:
    """Return `text` with `stamp` recorded under `generated_by`.

    Any frontmatter already present is preserved — this merges a key rather
    than replacing a block, because the synthesis-derived keys a generated
    document may carry (topic, owner, tier) are not this function's to
    discard.

    Written through the same `frontmatter` library `content/tree.py` parses
    with, so what is written is by construction what will be read back.
    """
    import frontmatter
    import mdformat

    from policyforge.textfile import loads_frontmatter

    parsed = loads_frontmatter(text)
    parsed.metadata[GENERATED_BY] = dict(stamp)
    # `dumps` emits frontmatter followed by the body, and appends no trailing
    # newline; the caller's text kept its own.
    stamped = frontmatter.dumps(parsed) + "\n"

    # Normalised through mdformat so a stamped document passes the same
    # markdown gate everything else here is held to. Two different YAML
    # dumpers are involved — python-frontmatter's and mdformat's — and they
    # disagree about list indentation, so without this every generated
    # document would be reported as badly formatted over two spaces of YAML.
    # The extension set matters: mdformat without `frontmatter` rewrites a
    # leading `---` into a thematic break and destroys the block underneath.
    return mdformat.text(stamped, extensions={"gfm", "frontmatter"})


def read_generated_by(metadata: dict) -> dict:
    """The `generated_by` block, or {} where the document carries none."""
    block = metadata.get(GENERATED_BY)
    return dict(block) if isinstance(block, dict) else {}


# -- has this document been edited since `generate` wrote it? (#385) ----------
#
# `generate` writes into the content tree people edit and pull into, so it
# must not replace a person's work. It records a hash of the BODY it wrote in
# the stamp, and the next run compares the file's body to it (80's ruling).
# Frontmatter is not part of the hash: it is rebuilt from the synthesis and
# the registry on every run, and `publish` and `pull` write into it.

#: The key in `generated_by` holding the hash of the body `generate` wrote.
BODY_SHA256 = "body_sha256"

#: What `edit_state` says about a document.
UNEDITED = "unedited"  # its body is what `generate` wrote
EDITED = "edited"  # it has a recorded hash and the body no longer matches
NO_HASH = "no-hash"  # stamped, but written before the hash was recorded
UNSTAMPED = "unstamped"  # no `generated_by` block at all, or unparseable


def body_digest(text: str) -> str:
    """The sha256 of `text`'s body: the document without its frontmatter and
    with surrounding whitespace dropped, so an editor's newline at the end of
    the file is not an edit. `frontmatter` folds CRLF to LF itself (measured,
    and held by a test), so a checkout with Windows line endings hashes the
    same."""
    import hashlib

    from policyforge.textfile import loads_frontmatter

    body = loads_frontmatter(text).content
    return hashlib.sha256(body.strip().encode("utf-8")).hexdigest()


def stamp_with_body_hash(text: str, stamp: dict) -> str:
    """`stamp_document`, with the hash of the body it ends up with recorded
    under `generated_by.body_sha256`.

    The hash is taken from the stored text, after the markdown normalisation
    `stamp_document` applies, and checked again after the stamp that holds it
    is written: a hash of a body the file doesn't have would call every
    untouched document edited.
    """
    stamped = stamp_document(text, stamp)
    for _ in range(3):
        digest = body_digest(stamped)
        again = stamp_document(stamped, {**stamp, BODY_SHA256: digest})
        if body_digest(again) == digest:
            return again
        stamped = again
    raise ValueError("the document body kept changing under markdown normalisation")


def edit_state(text: str) -> str:
    """Whether the document `text` is what `generate` last wrote there.

    `UNEDITED` only when it carries a recorded hash and its body matches.
    Everything else is a reason to ask before overwriting: a body that no
    longer matches, a stamp from before the hash was recorded, and a document
    `generate` never stamped (hand-written, imported, or unparseable).
    """

    import yaml

    from policyforge.textfile import loads_frontmatter

    try:
        metadata = loads_frontmatter(text).metadata
    except (ValueError, yaml.YAMLError):
        return UNSTAMPED
    block = metadata.get(GENERATED_BY)
    if not isinstance(block, dict):
        return UNSTAMPED
    recorded = block.get(BODY_SHA256)
    if not isinstance(recorded, str) or not recorded:
        return NO_HASH
    return UNEDITED if recorded == body_digest(text) else EDITED


# -- an import from Confluence (#197) ------------------------------------------
#
# A page pulled back from Confluence comes home as the page body only, and
# the frontmatter `generate` wrote is not in it. Two things were lost that
# way. One is attribution. The other is the licensed-content ceiling: the
# GitHub-wiki exporter refuses a public wiki to a document whose class is
# `licensed`, and a document with no class falls back to the wiki's
# visibility rule. So `import-confluence` writes its own record, and 80's
# ruling on #197 fixes what that record may say:
#
#   - `content_class` is CARRIED from a local predecessor, never invented.
#     With no predecessor it is left out, and the command says so. A
#     predecessor's value that isn't a known class is carried as `licensed`
#     (#336): read the most guarded way, not dropped.
#   - `imported_from` records the import. There is no `generated_by` block:
#     the page may have been edited by a person, and `models` on that text
#     would be a false attribution.
#   - the earlier generation, if any, is kept as `previously_generated_by`,
#     which nothing counts as a claim about the current text.

#: Where the import is recorded.
IMPORTED_FROM = "imported_from"
#: The predecessor's `generated_by`, kept as history and not as attribution.
PREVIOUSLY_GENERATED_BY = "previously_generated_by"
#: A top-level class. The wiki exporter reads it before `generated_by`'s.
CONTENT_CLASS = "content_class"


def declared_content_class(metadata: dict) -> str:
    """The content class a document states, normalised, or "".

    The same two places the wiki exporter reads: a top-level
    `content_class` and `generated_by.content_class`. Stripped and
    lower-cased, because frontmatter is hand-editable.

    **When both say something, the most guarded wins** (#339, 80's ruling).
    The top-level value used to win alone whenever present, so a hand-added
    `content_class: public-domain` lowered a licensed `generated_by` stamp in
    the Zardoz corpus, while the wiki exporter refused if either said
    licensed: two readers, one document, two answers. Ranked by
    `stamped_class`, so an unreadable value counts as licensed here too (and
    is the text returned, for `unknown_stamp` to name). On a tie the
    top-level text is returned.

    **Presence is `is not None`, not truthiness** (#336, 9b on PR 338). YAML
    reads `content_class: no` as False, `0` as 0 and `{}` as an empty mapping,
    and `or ""` turned each into "" (no class), while `stamped_class` reads
    the same value as present and licensed: the corpus and the wiki
    exporter disagreed about one stamp. A present value is kept as its text
    (`false`, `0`, `{}`), so `stamped_class` reads it as licensed and
    `unknown_stamp` names it. Only None, or an empty or whitespace string,
    is absent.
    """

    def _said(value) -> str:
        return "" if value is None else str(value).strip().lower()

    from policyforge.llm.boundary import CONTENT_CLASSES

    said = [
        text
        for text in (
            _said((metadata or {}).get(CONTENT_CLASS)),
            _said(read_generated_by(metadata or {}).get(CONTENT_CLASS)),
        )
        if text
    ]
    if not said:
        return ""
    # `max` keeps the first of equals, so a tie returns the top-level text.
    return max(said, key=lambda text: CONTENT_CLASSES.index(stamped_class(text)))


def stamped_class(value) -> str | None:
    """A stamped `content_class` as the boundary reads it (#336, 80's ruling).

    - **Absent** (missing, None, empty): None. Nothing was said, and every
      reader treats that as the organization's own, as before.
    - **A known class**, trimmed and casefolded: that class.
    - **Anything else** (`licenced`, `bogus`): `licensed`. A present value
      says the author meant to classify the document, and we can't tell how,
      so it's read the most guarded way. It used to be dropped as "no
      class", which let a misspelt licensed stamp through to a hosted model.

    The stamp-field twin of 320's `ingest.schema.is_licensed`, the envelope
    rule, and the same idea: fail closed on what can't be read. It isn't
    that function because the vocabularies differ: an envelope has two
    outcomes (public, or licensed), and a stamp has three known classes, so
    routing a stamp through `is_licensed` would read every `organization-
    internal` stamp as licensed. `unknown_stamp` gives the raw value for the
    one warning a reader prints per document.
    """
    from policyforge.llm.boundary import CONTENT_CLASSES, LICENSED

    text = str(value if value is not None else "").strip().casefold()
    if not text:
        return None
    return text if text in CONTENT_CLASSES else LICENSED


def unknown_stamp(value) -> str:
    """`value`, stripped, when it is stamped but not a known class; else ""
    (#336). What a reader names in its warning."""
    from policyforge.llm.boundary import CONTENT_CLASSES

    text = str(value if value is not None else "").strip()
    return text if text and text.casefold() not in CONTENT_CLASSES else ""


def carried_content_class(classes) -> str | None:
    """The most guarded class among what predecessors or inputs stamped.

    **The most guarded wins.** If the generated file says `licensed` and an
    older history record says `org-internal`, the import is `licensed`: a
    disagreement between predecessors must never be what lowers the
    ceiling. Each value is read by `stamped_class`, so **a value that isn't
    a known class counts as `licensed`** (#336). This used to drop it, on the
    reasoning that the wiki exporter read an unknown class as not licensed;
    both now fail closed. Returns None only when nothing was stamped at all.
    """
    from policyforge.llm.boundary import CONTENT_CLASSES

    read = {stamped_class(c) for c in classes} - {None}
    return max(read, key=CONTENT_CLASSES.index) if read else None


def import_metadata(
    *,
    imported_from: dict,
    content_class: str | None,
    previously_generated_by: dict | None = None,
) -> dict:
    """The frontmatter `import-confluence` writes on an imported document."""
    metadata: dict = {}
    if content_class:
        metadata[CONTENT_CLASS] = content_class
    metadata[IMPORTED_FROM] = dict(imported_from)
    if previously_generated_by:
        metadata[PREVIOUSLY_GENERATED_BY] = dict(previously_generated_by)
    return metadata


@dataclass(frozen=True)
class Attribution:
    """One document and what is known about who wrote it."""

    path: str
    models: tuple[str, ...] = ()
    provider_class: str = ""
    cost_usd: float | None = None
    #: Written since #379 (80's ruling): the known part of the cost, and how
    #: many calls had none. None on a stamp written before, which means "not
    #: recorded", never 0.
    cost_known_usd: float | None = None
    calls_unpriced: int | None = None
    #: Pulled back from Confluence by `import-confluence`, which records
    #: where the text came from rather than which model wrote it.
    imported: bool = False

    @property
    def stamped(self) -> bool:
        return bool(self.models)


def attribution(document) -> Attribution:
    """What `document`'s frontmatter says about the model that wrote it."""
    metadata = getattr(document, "metadata", {}) or {}
    block = read_generated_by(metadata)
    models = block.get("models") or []
    if isinstance(models, str):
        models = [models]
    cost = block.get("cost_usd")
    known = block.get("cost_known_usd")
    unpriced = block.get("calls_unpriced")
    return Attribution(
        path=getattr(document, "relative_path", ""),
        models=tuple(str(m) for m in models),
        provider_class=str(block.get("provider_class") or ""),
        cost_usd=float(cost) if isinstance(cost, (int, float)) else None,
        cost_known_usd=float(known) if isinstance(known, (int, float)) else None,
        calls_unpriced=unpriced
        if isinstance(unpriced, int) and not isinstance(unpriced, bool)
        else None,
        imported=isinstance(metadata.get(IMPORTED_FROM), dict),
    )


def documents_by_model(documents) -> dict[str, list[str]]:
    """Every stamped document, grouped by the model that wrote it.

    This is the question the whole mechanism is for. A model found to be
    weakening requirements produces a list of documents to re-check, and the
    list comes from the repository rather than from anyone's memory.
    """
    grouped: dict[str, list[str]] = {}
    for document in documents:
        found = attribution(document)
        for model in found.models:
            grouped.setdefault(model, []).append(found.path)
    return {model: sorted(paths) for model, paths in sorted(grouped.items())}


def attribution_summary(documents) -> str:
    """A human-readable account of who wrote what in a content tree."""
    found = [attribution(d) for d in documents]
    stamped = [a for a in found if a.stamped]
    imported = sorted(a.path for a in found if a.imported and not a.stamped)
    # An import is neither generated nor unstamped: it says where its text
    # came from, and that a model's authorship of it is not known (#197).
    imported_lines = (
        [
            "",
            f"  {len(imported)} document(s) imported from Confluence. They record "
            f"where the text came from, not which model wrote it, because the "
            f"page may have been edited by hand:",
        ]
        + [f"      {path}" for path in imported]
        if imported
        else []
    )
    if not stamped:
        return (
            f"No document in this tree records the model that wrote it "
            f"({len(found)} document(s) checked). A document is stamped when "
            f"`generate` writes it; one that was hand-written, or pulled back "
            f"from Confluence, carries no stamp and that is not a fault."
        ) + "\n".join([""] + imported_lines if imported_lines else [])

    lines = [f"{len(stamped)} of {len(found)} document(s) record how they were written:", ""]
    for model, paths in documents_by_model(documents).items():
        lines.append(f"  {model} — {len(paths)} document(s)")
        lines += [f"      {path}" for path in paths]
    lines += imported_lines

    unstamped = len(found) - len(stamped) - len(imported)
    if unstamped:
        lines += [
            "",
            f"  {unstamped} document(s) carry no stamp. That is not a finding: a "
            f"document written by hand, predating this, or pulled back from "
            f"Confluence has none, and nothing is inferred from absence.",
        ]
    return "\n".join(lines)


# -- regenerating over a document that already has frontmatter (#404) ----------
#
# `generate` used to build a document's frontmatter from scratch, so a hand-set
# `owner:`, a key somebody added, and the page version `pull` records inside
# `confluence:` were all replaced without a word (1d, on PR 402; the body hash
# there covers the body only). 80's ruling: merge, as `pull` does. `generate`
# updates the keys it owns and keeps every other one.

#: The keys `generate` writes from its own inputs: the stamp, and the topic
#: and tier from the synthesis and the command line.
OWNED_KEYS = frozenset({GENERATED_BY, "topic", "tier", "policy"})

#: What belongs to one topic's page and owner, and so is not carried onto a
#: document of another topic.
TOPIC_BOUND_KEYS = ("confluence", "targets", "owner")


def existing_metadata(text: str | None) -> dict:
    """The frontmatter of the document already at the target, or {} where
    there is none or it doesn't parse (which the overwrite guard has already
    asked about)."""
    if not text:
        return {}

    import yaml

    from policyforge.textfile import loads_frontmatter

    try:
        return dict(loads_frontmatter(text).metadata)
    except (ValueError, yaml.YAMLError):
        return {}


def merge_frontmatter(
    draft: dict,
    existing: dict,
    *,
    tier: str,
    topic: str,
    owner: str,
    binding: dict | None,
    policy: str | None = None,
) -> tuple[dict, str]:
    """The frontmatter a regenerated document is written with, and the one
    line to print when a hand-set owner was kept over the synthesis's.

    - `generated_by` is not here: the caller stamps it, always.
    - `topic` and `tier` are `generate`'s: set from the synthesis and the
      command line (an absent topic leaves the document's own).
    - **every other key of the existing document is kept**, `owner` and the
      `confluence:` binding and its pulled `version` included, **when it is
      the same document**: if its `topic` names another topic than the one
      being written, what belongs to that topic's page (`confluence`,
      `targets`, `owner`) is not carried, and the line says so. A binding
      carried across would have `publish` write this topic's text over the
      other topic's page (9b, on PR 407).
    - `owner`: the synthesis's value fills a document with none. One already
      there that differs is kept, and named in the line, because a person set
      it (or `pull` carried it) and the synthesis can't tell which.
    - `confluence:` from the registry fills what the document's own block
      lacks, wherever the document spells it (`confluence:` or
      `targets: {confluence: ...}`, as `pull` does); it never overwrites a
      value the document has.
    """
    metadata = dict(draft)
    carried = {key: value for key, value in existing.items() if key not in OWNED_KEYS}
    note = ""
    earlier = existing.get("topic")
    if (
        topic
        and earlier not in (None, "")
        and str(earlier).strip().lower() != topic.strip().lower()
    ):
        left = [key for key in TOPIC_BOUND_KEYS if key in carried]
        for key in left:
            del carried[key]
        existing = {k: v for k, v in existing.items() if k not in TOPIC_BOUND_KEYS}
        if left:
            note = (
                f"The document was about {str(earlier)!r}, not {topic!r}, so its "
                f"{', '.join(left)} {'was' if len(left) == 1 else 'were'} not carried over."
            )
    metadata.update(carried)
    metadata["tier"] = tier
    # The parent Policy of a Standard is `generate`'s, from the synthesis (22,
    # part 2): a regenerated Standard is written with the registry's current
    # one, or with none. `None` is a tier that has no parent Policy to write.
    if policy:
        metadata["policy"] = policy
    elif policy is None and "policy" in existing:
        metadata["policy"] = existing["policy"]
    if topic:
        metadata["topic"] = topic
    elif "topic" in existing:
        metadata["topic"] = existing["topic"]

    kept = existing.get("owner")
    if kept not in (None, ""):
        metadata["owner"] = kept
        if owner and str(kept) != owner:
            note = (  # one line: the topic note above has dropped `owner` already
                f"Kept the owner already in the document, {str(kept)!r}; the synthesis "
                f"names {owner!r}. Remove the `owner:` line to take the synthesis's."
            )
    elif owner:
        metadata["owner"] = owner

    if binding:
        current = metadata.get("confluence")
        targets = metadata.get("targets")
        under_targets = isinstance(targets, dict) and isinstance(targets.get("confluence"), dict)
        if current is None and under_targets:
            # The document spells its binding under `targets:`: that block is
            # the one filled, so it doesn't end up with two for one store.
            metadata["targets"] = {
                **targets,
                "confluence": {**binding, **targets["confluence"]},
            }
        elif current is None:
            metadata["confluence"] = dict(binding)
        elif isinstance(current, dict):
            metadata["confluence"] = {**binding, **current}
    return metadata, note
