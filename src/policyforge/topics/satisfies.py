"""What a published document can be shown against: the assessor's direction.

`addresses` answers "who answers for AC-2". `coverage` answers it in
aggregate. Neither answers the question an assessor actually opens with —
*this document, what does it satisfy?* — and answering it by hand means
reading every citation in the text and following each one through the
crosswalk.

**The evidence is the citations in the document, not the topic's anchors.**
A topic anchor is an intention: somebody wrote down that this team owns
AC-2. A citation in published text is the thing an assessor can be shown. So
a requirement reaches this report only when a document cites it, or cites a
control the crosswalk maps it to, and a requirement the topic anchors while
its documents never mention it is reported apart, under its own heading. That
gap is the most useful line here: it is the difference between what a
programme claims and what it has written down.

**Every mapping says where it came from.** A pair from the catalog's own
published crosswalk, a pair the organization reviewed and accepted, and a
pair sitting in an overlay that nobody has looked at yet are three different
strengths of evidence, and an assessor is entitled to tell them apart. The
unreviewed ones are listed and marked rather than hidden: a report that
quietly dropped them would look better than the evidence behind it, which is
the failure this project exists to avoid. Where a pair's relationship is in
`coverage.PARTIAL_RELATIONSHIPS` — `superset` or `intersects` recorded by a
reviewer, or `source-untyped` declared by the catalog's own manifest for a
mapping its source publishes untyped and incomplete (#408) — the control
covers part of the requirement, and it is reported as *in part*, never as
satisfied.

**What counts as a citation here.** The population is
`content/tags.SOURCE_TAG_RE`, shared with `content/check`,
`content/deontic`, `edit/apply` and `frameworks/drift`, so every reader
agrees on what a tag is. It is a shape rather
than a list of framework names: a capitalised name followed by a token
carrying a digit. What it excludes is a bracket with no digit-bearing
identifier — `[Ticketing System]`, `[Assignment: organization-defined ...]`,
`[HIPAA Documentation Review Frequency]` — which are the organisational
placeholders `generate` writes on purpose, not citations.

Deterministic: no model call and no network, like `coverage` and
`addresses`. Requirement identifiers are printed; requirement text and
titles are not, which is the same line the other analyses hold and keeps a
licensed catalog's prose out of a report that gets pasted around.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from policyforge.mapping.crosswalk import NIST_ANCHOR, normalize_framework
from policyforge.topics.anchoring import anchor_keys, families_for, regulatory_families
from policyforge.topics.coverage import PARTIAL_RELATIONSHIPS

_HEADING_RE = re.compile(r"^#{1,6}\s+(?P<title>.+?)\s*$")

PUBLISHED = "published crosswalk"
REVIEWED = "overlay, reviewed"
UNREVIEWED = "overlay, NOT REVIEWED"

#: Relationships under which the control covers only part of the requirement.
#: `coverage`'s set itself, not a copy of it: the copy here lacked
#: `source-untyped` (#408) and printed NIST's untyped CSF links as satisfied.
PARTIAL = PARTIAL_RELATIONSHIPS

#: A requirement reached through a source's link to a whole 800-53 family
#: (#448): never satisfied, and never a control-level pair.
FAMILY_ROUTE = "family"


@dataclass
class Citation:
    """One requirement named in a document, and where."""

    framework: str
    requirement_id: str
    #: What the tag said after the id, if anything — a baseline ("Moderate,
    #: High"), HIPAA's "Addressable", ARC-AMPE's "AE Mandatory". Kept as
    #: written: it is the framework's own word for how the requirement
    #: applies, and paraphrasing it would be inventing a vocabulary.
    qualifier: str = ""
    sections: list[str] = field(default_factory=list)
    #: How many times this requirement is cited in the document. Distinct
    #: from the row itself, which is one per (framework, id): a control
    #: cited in nine sections is one row and nine occurrences, and the two
    #: answer different questions.
    occurrences: int = 0

    @property
    def key(self) -> tuple[str, str]:
        return (self.framework, self.requirement_id)


@dataclass
class Reached:
    """A requirement reached from a cited control through the crosswalk."""

    framework: str
    requirement_id: str
    via: str
    provenance: str = PUBLISHED
    relationship: str = "unspecified"
    reviewed_by: str = ""
    flags: list[str] = field(default_factory=list)
    sections: list[str] = field(default_factory=list)
    #: `crosswalked` (a control-level pair) or `family` (#448): the source
    #: linked the requirement to a WHOLE 800-53 family, and the document
    #: cites a control in it. `via` is then the family, never a control.
    route: str = "crosswalked"

    @property
    def in_part(self) -> bool:
        # A family link is evidence toward the requirement, never coverage of
        # it (80's ruling on #448), whatever relationship it carries.
        return self.route == FAMILY_ROUTE or self.relationship in PARTIAL

    @property
    def reviewed(self) -> bool:
        return self.provenance == REVIEWED


@dataclass
class DocumentEvidence:
    path: str
    title: str
    tier: str = ""
    topic: str = ""
    cited: list[Citation] = field(default_factory=list)
    reached: list[Reached] = field(default_factory=list)
    #: Anchors of the document's topic that no document of that topic cites.
    anchored_not_cited: list[str] = field(default_factory=list)
    #: Which documents were searched before calling an anchor uncited — the
    #: topic's whole set, or this file alone under `--document`. Printed, so
    #: a topic-level gap is never misread as a file-level one.
    anchored_scope: str = ""
    #: Cited identifiers that match no requirement in any loaded catalog,
    #: deduplicated. `unknown_occurrences` counts every instance.
    unknown: list[str] = field(default_factory=list)
    unknown_occurrences: int = 0

    @property
    def unreviewed(self) -> list[Reached]:
        return [r for r in self.reached if r.provenance == UNREVIEWED]

    @property
    def nist_anchors(self) -> list[str]:
        """The cited NIST ids the crosswalk traversal starts from.

        The crosswalk is NIST-anchored, so a document citing no NIST
        requirement reaches nothing through it however well cited it is.
        Named here because the report has to be able to say that, rather
        than print an empty section a reader will take for an absence of
        coverage.
        """
        return [c.requirement_id for c in self.cited if c.framework == NIST_ANCHOR]

    @property
    def occurrences(self) -> int:
        """Every citation instance in this document, resolved or not.

        The headline denominator. Reported beside the distinct count
        because the two are different questions — "how many citations does
        this document make" against "how many requirements does it name" —
        and a fraction quoted without saying which is not checkable.
        """
        return sum(c.occurrences for c in self.cited) + self.unknown_occurrences

    @property
    def distinct(self) -> int:
        """Unique (framework, requirement id) pairs named in this document."""
        return len(self.cited) + len(self.unknown)


def resolve_framework(written: str, ids=None) -> str:
    """The catalog a citation's framework name points at, or "" if none does.

    Tags abbreviate. The catalogs declare `NIST 800-53`, `HIPAA Security
    Rule` and `ARC-AMPE`, while the convention the synthesis prompt teaches
    by example (`[NIST IA-5 | GovRAMP IA-5]`) writes the short form, and no
    rule anywhere says what the short form of a given name is.

    So an abbreviation resolves when it names exactly one loaded catalog:
    `ARC` reaches `arc-ampe` for the same reason `NIST` reaches `nist`.
    Requiring the declared name would make unknowns of most real tags;
    accepting a prefix that matches two catalogs would silently pick one,
    so an ambiguous one resolves to nothing and is reported. Only at a
    token boundary, so `NIS` is not an abbreviation of anything.

    The uniqueness rule is doing real work rather than being defensive:
    load 800-53 and 800-171 together and `NIST` stops naming one catalog,
    which is a citation an assessor genuinely cannot follow.
    """
    index = ids or {}
    name = written.strip().casefold()
    if not name:
        return ""
    if name in index:
        return name
    # `NIST 800-53` and `HIPAA Security Rule` are declared names, which
    # `normalize_framework` maps onto the key the catalogs are filed under.
    narrowed = normalize_framework(written)
    if narrowed in index:
        return narrowed
    matches = {
        k for k in index if k.startswith(name) and not k[len(name) : len(name) + 1].isalnum()
    }
    return matches.pop() if len(matches) == 1 else ""


def split_citation(part: str, frameworks: list[str], ids=None) -> tuple[str, str, str]:
    """One citation's (framework, requirement id, qualifier).

    None of the three can be found by counting words, in either direction.
    Framework names carry spaces — the generator writes `NIST 800-53 IR-1`
    and `HIPAA Security Rule 164.308(a)(6)(i)`, not `NIST IR-1` — so taking
    the first word as the framework leaves `800-53 IR-1` as the identifier.
    Requirement ids carry spaces too: HITRUST builds them as `01.a Level
    1`, so taking the last word fails from the other end. And a tag may end
    in a qualifier, which the synthesis prompt asks for ("Include the
    baseline in the tag when the source control specifies one, e.g.
    `[GovRAMP IA-5 Moderate]`") and whose vocabulary is per-framework and
    open-ended: `IR-4(1) Moderate, High`, `164.310(a)(2)(i) Addressable`,
    `CP-2 AE Mandatory`.

    So neither end is guessed. The framework is split on the names the
    loaded catalogs declare, longest first and only at a word boundary;
    then the identifier is the *longest run of words that the catalog
    actually has an id for*, and whatever follows is the qualifier. Asking
    the catalog means there is no vocabulary to keep up to date, and
    HITRUST's `01.a Level 1` resolves whole because the catalog says that
    is one id.

    A citation whose id resolves to nothing is returned entire, with no
    qualifier split off, so it is reported as written rather than as a
    truncation this function invented.
    """
    lowered = part.casefold()
    # Longest first HERE, not only in `parse_citations`: this docstring says
    # the split is longest-first, and a caller passing names in any other
    # order got the SHORTER one the day a catalog name became a prefix of
    # another -- "NIST AI RMF" of "NIST AI RMF Playbook", whose actions then
    # split as the Core with id "Playbook ..." (#177). Ordering here makes
    # the contract the function's, not every caller's.
    for name in sorted(frameworks, key=len, reverse=True):
        if lowered.startswith(name.casefold()) and part[len(name) : len(name) + 1].isspace():
            framework, rest = name, part[len(name) :].strip()
            break
    else:
        framework, _, rest = part.partition(" ")
        rest = rest.strip()

    words = rest.split()
    known = (ids or {}).get(resolve_framework(framework, ids), ())
    for count in range(len(words), 0, -1):
        candidate = " ".join(words[:count])
        if candidate in known:
            return framework, candidate, " ".join(words[count:])
    return framework, rest, ""


def parse_citations(body: str, frameworks=(), ids=None) -> list[tuple[str, str, str, str]]:
    """(framework, requirement id, qualifier, section) for each citation in `body`.

    A tag is `[NIST 800-53 AC-2 | HIPAA Security Rule 164.308(a)(3)(i)]` —
    one or more citations separated by `|`. The pattern matching the tag
    itself is `content/tags.SOURCE_TAG_RE`, which every reader of tags now
    shares: this report has to agree with the checker about what a citation
    *is*, and two regexes would eventually disagree.

    The section is the nearest heading above the citation, so a reader can
    be pointed at the paragraph rather than the file.

    A heading line is scanned for tags like any other, and is its own
    section. Procedures put the tag on the step heading — `### Test the
    contingency plan [NIST CP-4 ... | NIST CP-4(1)-(5)]` — so a scan that
    read a heading only for its title and moved on lost every citation
    carried there. Silently: the requirements were simply absent from the
    report, which is the failure this command exists to catch.
    """
    from policyforge.content.tags import SOURCE_TAG_RE, known_framework_names, tag_parts

    # The catalogs loaded, plus every catalog on disk: the ONE list of names
    # the Playbook gate also reads (#340), so a part naming a framework that
    # is not loaded is still that framework's, and is reported unknown rather
    # than inherited by the part before it.
    known = sorted({str(f) for f in frameworks} | known_framework_names(), key=len, reverse=True)
    found: list[tuple[str, str, str, str]] = []
    section = ""
    for line in body.splitlines():
        heading = _HEADING_RE.match(line.strip())
        if heading:
            # Without the tags, which are traceability rather than title.
            section = SOURCE_TAG_RE.sub("", heading.group("title")).strip()
        for tag in SOURCE_TAG_RE.findall(line):
            # One splitter for every reader of a tag's parts, shared with the
            # Playbook gate (#333): a shorthand part inherits the preceding
            # part's framework, and `split_citation` then resolves it -- or
            # returns it whole, so it is reported unresolved like any other.
            for part in tag_parts(tag, known, lambda word: bool(resolve_framework(word, ids))):
                framework, requirement_id, qualifier = split_citation(part.citation, known, ids)
                if framework and requirement_id:
                    found.append((framework, requirement_id, qualifier, section))
    return found


def _catalog_index(controls) -> dict[str, set[str]]:
    """{normalized framework: every requirement id it holds}."""
    index: dict[str, set[str]] = {}
    for control in controls:
        ids = index.setdefault(normalize_framework(control.framework), set())
        ids.add(control.control_id)
        ids.update(e.enhancement_id for e in control.enhancements)
        ids.update(r.requirement_id for r in control.requirements)
    return index


def document_evidence(
    document,
    *,
    controls,
    crosswalk: dict[str, dict[str, list[str]]],
    provenance: dict[tuple[str, str, str], object] | None = None,
    declared_relationships: dict[str, str] | None = None,
    family_links: dict[str, dict[str, frozenset[str]]] | None = None,
) -> DocumentEvidence:
    """What one document can be shown against.

    `family_links` is each catalog's links to whole 800-53 families
    (`declared_family_links`, #448), defaulting to the manifests on disk. A
    document citing a control in a linked family reaches the linked
    requirement by the `family` route: in part, never satisfied.

    `anchored_not_cited` is left empty here and filled by `build_report`,
    which can see the topic's other documents; see its docstring for why
    that question cannot be answered one file at a time.

    `provenance` maps (framework, requirement id, anchor id) to the overlay
    row that accepted the pair, from `crosswalk.overlay.accepted_rows`. A
    pair with no entry came from the catalog's published crosswalk.

    `controls` must be the catalogs as `load_catalogs` returns them, with
    overlays already applied, since the crosswalk is read from them.

    `declared_relationships` is each framework's declared crosswalk relationship
    (`declared_crosswalk_relationships`, #408), which a published pair with
    no overlay row carries, as `relationships_for` gives it to `coverage`.
    It defaults to the manifests on disk.
    """
    if declared_relationships is None:
        declared_relationships = _declared_relationships()
    if family_links is None:
        family_links = _declared_family_links()
    index = _catalog_index(controls)
    evidence = DocumentEvidence(
        path=getattr(document, "relative_path", str(getattr(document, "path", ""))),
        title=document.title,
        tier=getattr(document, "tier", ""),
        topic=getattr(document, "topic", ""),
    )

    cited: dict[tuple[str, str], Citation] = {}
    names = {c.framework for c in controls}
    declared = families_for(controls)
    for framework, requirement_id, qualifier, section in parse_citations(
        document.body, names, index
    ):
        catalog = resolve_framework(framework, index)
        # Checked against the framework the citation names, not against every
        # id in every catalog: `[HIPAA AC-2]` names a real NIST control under
        # the wrong framework, and an assessor following it finds nothing.
        # An unresolvable framework name gives an empty key, which no
        # catalog holds, so it lands in `unknown` with the id as written.
        if requirement_id in index.get(catalog, ()):
            standing_for = [requirement_id]
        else:
            # A regulatory catalog's requirement cited by a paragraph the
            # catalog does not carry: `[HIPAA Security Rule 164.308(a)(1)]`
            # is the standard `164.308(a)(1)(i)` (80's ruling on #423, one
            # resolution for drift and for this report).
            standing_for = sorted(regulatory_families(requirement_id, declared.get(catalog, {})))
        if not standing_for:
            label = f"{framework} {requirement_id}"
            evidence.unknown_occurrences += 1
            if label not in evidence.unknown:
                evidence.unknown.append(label)
            continue
        for resolved in standing_for:
            key = (catalog, resolved)
            citation = cited.setdefault(key, Citation(catalog, resolved, qualifier))
            citation.occurrences += 1
            if section and section not in citation.sections:
                citation.sections.append(section)
    evidence.cited = sorted(cited.values(), key=lambda c: (c.framework, c.requirement_id))

    anchor_ids = set(evidence.nist_anchors)
    reached: dict[tuple[str, str], Reached] = {}
    for anchor in sorted(anchor_ids):
        sections = next(
            (c.sections for c in evidence.cited if c.key == (NIST_ANCHOR, anchor)),
            [],
        )
        for raw_framework, requirement_ids in sorted(crosswalk.get(anchor, {}).items()):
            framework = normalize_framework(raw_framework)
            for requirement_id in sorted(requirement_ids):
                key = (framework, requirement_id)
                if key in cited or key in reached:
                    continue
                row = (provenance or {}).get((framework, requirement_id, anchor))
                reached[key] = Reached(
                    framework=framework,
                    requirement_id=requirement_id,
                    via=anchor,
                    provenance=_provenance_of(row),
                    relationship=(getattr(row, "relationship", "") if row is not None else "")
                    or declared_relationships.get(framework, "")
                    or "unspecified",
                    reviewed_by=str((getattr(row, "reviewed_by", {}) or {}).get("who", "")),
                    flags=list(getattr(row, "flags", []) or []),
                    sections=list(sections),
                )

    # Family links (#448, 80's ruling): a requirement its source linked to a
    # whole 800-53 family is reached when the document cites a control in
    # that family. Only for a catalog that is loaded, and only where no
    # control-level pair already reached it: a family link adds nothing
    # beside one, and must never stand in for one.
    in_family: dict[str, list[str]] = {}
    for citation in evidence.cited:
        if citation.framework == NIST_ANCHOR:
            sections = in_family.setdefault(citation.requirement_id.split("-")[0].upper(), [])
            sections.extend(s for s in citation.sections if s not in sections)
    for framework, links in sorted(family_links.items()):
        if framework not in index:
            continue
        for requirement_id, families in sorted(links.items()):
            key = (framework, requirement_id)
            hit = sorted(f for f in families if f in in_family)
            if not hit or key in cited or key in reached:
                continue
            reached[key] = Reached(
                framework=framework,
                requirement_id=requirement_id,
                via=", ".join(hit),
                relationship="family",
                sections=[s for f in hit for s in in_family[f]],
                route=FAMILY_ROUTE,
            )
    evidence.reached = sorted(reached.values(), key=lambda r: (r.framework, r.requirement_id))
    return evidence


def _answers_for_an_anchor(framework: str) -> bool:
    """Whether a citation of `framework` can answer for a topic's anchor (#335).

    A TOPIC-anchor question, so `anchors_a_topic` answers it. This used to
    test `NIST_ANCHOR`, the crosswalk's key, so no AI RMF citation ever
    counted and every AI anchor read as cited nowhere: 19 of 19 on the
    shipped registry's generated Standards. **The Playbook stays out**: its
    ids are the Core's (`Govern 1.1`), so crediting it would let a document
    citing only NIST's suggestions answer for a Core anchor.

    Its own function because this file is also a crosswalk-anchor site, and
    `test_anchor_concepts` guards the two sides function by function here:
    this is the only code in the file allowed to name the topic anchor, and
    the import stays inside it so nothing at module level does.
    """
    from policyforge.mapping.crosswalk import anchors_a_topic

    return anchors_a_topic(framework)


def _declared_relationships() -> dict[str, str]:
    from policyforge.frameworks.registry import (
        config_or_defaults,
        declared_crosswalk_relationships,
    )

    return declared_crosswalk_relationships(config_or_defaults("crosswalk relationships were read"))


def _declared_family_links() -> dict[str, dict[str, frozenset[str]]]:
    from policyforge.frameworks.registry import config_or_defaults, declared_family_links

    return declared_family_links(config_or_defaults("family links were read"))


def build_report(
    documents,
    *,
    controls,
    crosswalk: dict[str, dict[str, list[str]]],
    provenance: dict[tuple[str, str, str], object] | None = None,
    topics=(),
    declared_relationships: dict[str, str] | None = None,
) -> list[DocumentEvidence]:
    """Evidence for each of `documents`, in the order given.

    "Anchored but cited nowhere" is settled across a topic's documents
    together rather than per file. A topic's Policy, Standard and Procedure
    share its anchors between them — the Procedure is usually where a
    control is actually cited — so asking the question of one document at a
    time would report the Policy as missing anchors its own Procedure
    cites, which is noise, and noise in the one section an assessor should
    read first.

    Only documents present in `documents` count as citing. Narrowing to one
    topic therefore still gives that topic a true answer, while `--document`
    on a single file reports against that file alone, which is what naming
    one file asks for.
    """
    from policyforge.zardoz.corpus import slugify

    # Keyed by slug, not by name, and the document is matched on its
    # frontmatter topic *or* its filename: `generate` names the file for the
    # topic's slug but writes no `topic:` — that arrives at publish — so a
    # name-only match would report nothing for a tree that was just
    # generated. See the same fallback in the `satisfies` command.
    anchors_of = {slugify(t.name): list(t.nist_controls) for t in topics}
    if declared_relationships is None:
        declared_relationships = _declared_relationships()
    evidences = []
    keys = []
    for document in documents:
        evidence = document_evidence(
            document,
            controls=controls,
            crosswalk=crosswalk,
            provenance=provenance,
            declared_relationships=declared_relationships,
        )
        slug = slugify(evidence.topic)
        if slug not in anchors_of:
            slug = slugify(getattr(document, "slug", "") or "")
        evidences.append(evidence)
        keys.append(slug)

    cited_by_topic: dict[str, set[str]] = {}
    for evidence, slug in zip(evidences, keys, strict=True):
        reached = cited_by_topic.setdefault(slug, set())
        for citation in evidence.cited:
            if not _answers_for_an_anchor(citation.framework):
                continue
            # The citation, and the control it hangs off: citing AC-2(3) is
            # mentioning AC-2, and citing Govern 1.1 is mentioning Govern 1.
            # `coverage` reads the relation the other way round (anchoring
            # AC-2 claims AC-2(3)) through the same rule (#318, #377), so a
            # topic that cites IR-3(1) and IR-3(3) is not reported as never
            # mentioning IR-3.
            reached |= anchor_keys(citation.requirement_id, citation.framework)
    searched = Counter(keys)
    for evidence, slug in zip(evidences, keys, strict=True):
        if slug in anchors_of:
            evidence.anchored_not_cited = sorted(
                set(anchors_of[slug]) - cited_by_topic.get(slug, set())
            )
            # Said out loud rather than left to the reader. The same list
            # means two different things depending on how much was searched,
            # and a topic-level gap misread as a file-level one sends
            # somebody to rewrite a Policy that was never the problem.
            count = searched[slug]
            evidence.anchored_scope = (
                f"across this topic's {count} documents" if count > 1 else "in this document alone"
            )
    return evidences


def _provenance_of(row) -> str:
    """How strong the evidence for one accepted pair is.

    Three cases, and the middle one is why this is not just a truth test on
    `reviewed_by`: `seed_overlay` writes the catalog's published pairs in as
    accepted with `sources: [published]` and no reviewer, because nobody
    reviewed them — they came with the catalog. Those are published-crosswalk
    evidence, exactly as they were before the overlay existed, and calling
    them unreviewed would flag every row of a freshly seeded file.

    What is left is a pair accepted in the file with no reviewer and no
    publisher behind it: a model proposal somebody marked accepted by hand
    rather than through `crosswalk review`. That is the one an assessor
    needs pointed out.
    """
    if row is None:
        return PUBLISHED
    if getattr(row, "reviewed_by", None):
        return REVIEWED
    if "published" in (getattr(row, "sources", None) or []):
        return PUBLISHED
    return UNREVIEWED


def as_records(evidences: list[DocumentEvidence]) -> list[dict]:
    """The JSON form. A contract once anything scripts it, so field names stay."""
    return [
        {
            "document": e.path,
            "title": e.title,
            "tier": e.tier,
            "topic": e.topic,
            "cited": [
                {
                    "framework": c.framework,
                    "requirement_id": c.requirement_id,
                    "qualifier": c.qualifier,
                    "occurrences": c.occurrences,
                    "sections": c.sections,
                    "route": "cited",
                }
                for c in e.cited
            ],
            "reached": [
                {
                    "framework": r.framework,
                    "requirement_id": r.requirement_id,
                    "route": r.route,
                    "via": r.via,
                    "provenance": r.provenance,
                    "relationship": r.relationship,
                    "in_part": r.in_part,
                    "reviewed_by": r.reviewed_by,
                    "flags": r.flags,
                    "sections": r.sections,
                }
                for r in e.reached
            ],
            "nist_anchors": e.nist_anchors,
            "anchored_not_cited": e.anchored_not_cited,
            "anchored_scope": e.anchored_scope,
            "unknown_citations": e.unknown,
            "unknown_occurrences": e.unknown_occurrences,
            # Both denominators, named. A fraction quoted without saying
            # which one it counts cannot be checked by the person reading it.
            "totals": {
                "citation_occurrences": e.occurrences,
                "distinct_citations": e.distinct,
                "unresolved_occurrences": e.unknown_occurrences,
                "unresolved_distinct": len(e.unknown),
            },
        }
        for e in evidences
    ]


def _by_framework(items) -> dict[str, list]:
    grouped: dict[str, list] = {}
    for item in items:
        grouped.setdefault(item.framework, []).append(item)
    return grouped


def format_report(evidences: list[DocumentEvidence]) -> str:
    lines: list[str] = []
    for evidence in evidences:
        lines.append(f"{evidence.path} — {evidence.title}")
        lines.append("=" * 60)
        if evidence.topic:
            lines.append(f"  topic: {evidence.topic}")
        lines.append(
            f"  {evidence.occurrences} citation(s), "
            f"{evidence.distinct} distinct requirement(s); "
            f"{evidence.unknown_occurrences} resolving to nothing"
        )
        if not evidence.cited:
            lines.append("  Cites no framework requirement. Nothing here is traceable.")
        for framework, citations in sorted(_by_framework(evidence.cited).items()):
            lines.append("")
            lines.append(f"Cited directly — {framework.upper()} ({len(citations)})")
            lines.append("-" * 60)
            for citation in citations:
                where = ", ".join(citation.sections) or "no section heading"
                shown = citation.requirement_id
                if citation.qualifier:
                    shown += f" ({citation.qualifier})"
                lines.append(f"  {shown:<28} {where}")
        crosswalked = [r for r in evidence.reached if r.route != FAMILY_ROUTE]
        for framework, reached in sorted(_by_framework(crosswalked).items()):
            lines.append("")
            lines.append(f"Reached through the crosswalk — {framework.upper()} ({len(reached)})")
            lines.append("-" * 60)
            for item in reached:
                extent = "in part" if item.in_part else "satisfied"
                note = item.provenance
                if item.reviewed_by:
                    note += f" by {item.reviewed_by}"
                if item.relationship != "unspecified":
                    note += f", {item.relationship}"
                if item.flags:
                    note += f", {', '.join(item.flags)}"
                lines.append(f"  {item.requirement_id:<24} {extent:<10} via {item.via}  [{note}]")
        families = [r for r in evidence.reached if r.route == FAMILY_ROUTE]
        for framework, reached in sorted(_by_framework(families).items()):
            # 80's ruling on #448: in part, never satisfied, and saying why.
            lines.append("")
            lines.append(f"Reached through a family link — {framework.upper()} ({len(reached)})")
            lines.append("-" * 60)
            for item in reached:
                lines.append(
                    f"  {item.requirement_id:<24} {'in part':<10} via the {item.via} family  "
                    "[its source linked it to the whole family, not to any control in it]"
                )
        if evidence.cited and not evidence.reached and not evidence.nist_anchors:
            lines.append("")
            lines.append("Reached through the crosswalk — none, and not for want of coverage")
            lines.append("-" * 60)
            lines.append(
                "  The crosswalk is anchored on NIST 800-53, so it is walked from the "
                "NIST requirements a document cites. This one cites none, so there is "
                "nothing to walk from. That is a property of the crosswalk, not a "
                "finding about the document."
            )
        unreviewed = [r for r in evidence.reached if r.provenance == UNREVIEWED]
        if unreviewed:
            lines.append("")
            lines.append(
                f"  {len(unreviewed)} mapping(s) above rest on an overlay row marked "
                "accepted by hand, NOT accepted through `policyforge crosswalk review`. "
                "Nobody has recorded that they checked it. They are shown because hiding "
                "them would make this report look stronger than the evidence behind it. "
                "Put them through review before handing this to an assessor."
            )
        if evidence.anchored_not_cited:
            lines.append("")
            scope = f" — searched {evidence.anchored_scope}" if evidence.anchored_scope else ""
            lines.append(
                f"Anchored by the topic, cited nowhere ({len(evidence.anchored_not_cited)}){scope}"
            )
            lines.append("-" * 60)
            lines.append(
                "  The registry says this topic answers for these; nothing searched "
                "mentions them. This is the gap between what is claimed and what is "
                "written. Citing an enhancement counts as citing its control, so AC-2 "
                "is not listed here when only AC-2(3) is cited."
            )
            for index in range(0, len(evidence.anchored_not_cited), 8):
                lines.append("  " + ", ".join(evidence.anchored_not_cited[index : index + 8]))
        if evidence.unknown:
            lines.append("")
            lines.append(f"Cited but unknown to every loaded catalog ({len(evidence.unknown)})")
            lines.append("-" * 60)
            lines.append(
                "  Either mistyped, or from a framework nobody loaded. An assessor "
                "following one of these finds nothing."
            )
            lines.append("  " + ", ".join(evidence.unknown))
        lines.append("")
    return "\n".join(lines)
