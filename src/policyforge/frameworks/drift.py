"""What a framework update actually changed, and what it costs you.

A catalog bumps version and the honest question is not "what is different"
— a diff answers that and is unreadable — but **"what do I have to go and
look at?"**. Rev 5.1.1 to 5.2.0 touches a few dozen controls out of a
thousand. Somewhere behind those sits a handful of your topics, a smaller
handful of your published documents, and possibly a parameter you decided on
the strength of wording that has since moved. Everything else is unaffected
and should stay unread.

Without that scoping the two available responses are both wrong. Re-generate
the whole document set, and you throw away every hand edit and every review
that ever happened to it. Change nothing, and the documents quietly stop
matching the catalog they cite. What makes the difference is a blast radius
you can trust, which is why this walks all the way through to *documents*
rather than stopping at control identifiers.

The workflow it is built for is the repo-backed one. `etl-oscal` overwrites
the catalog in place; git still holds the version you had. So the default
comparison is the working copy against the committed one, and "slurp up the
update" is two commands with a report in between:

    policyforge etl-oscal
    policyforge drift --controls data/frameworks/nist-800-53-r5/controls.json

**Not every change is worth reading.** A reworded discussion paragraph is
not a new obligation, so changes are separated by what they touch: the
control statement is the requirement and warrants review, while discussion,
title and related-control edits are noted and kept out of the way. Treating
them alike is how a drift report becomes something people skim past.
"""

from __future__ import annotations

import difflib
import subprocess  # nosec B404
from dataclasses import dataclass, field
from pathlib import Path

ADDED = "added"
REMOVED = "removed"
CHANGED = "changed"

#: Fields whose change alters what the organization must *do*. Everything
#: else is editorial: worth recording, not worth re-opening a document over.
#: `description` is an enhancement's requirement text (#369).
SUBSTANTIVE_FIELDS = ("control_statement", "description", "baseline", "enhancements", "parameters")


def _normalize(text: str) -> str:
    """Collapse whitespace so a reflow does not read as a rewrite."""
    return " ".join((text or "").split())


@dataclass
class ControlChange:
    """One control that arrived, left, or is not what it was."""

    control_id: str
    kind: str
    title: str = ""
    #: Which fields differ. Named rather than summarised so a reader can
    #: decide whether this one matters to them.
    fields: list[str] = field(default_factory=list)
    #: A few lines of the statement diff, for changes to the requirement.
    detail: str = ""

    @property
    def substantive(self) -> bool:
        """Whether this changes what the organization has to do."""
        if self.kind in (ADDED, REMOVED):
            return True
        return any(f in SUBSTANTIVE_FIELDS for f in self.fields)


@dataclass
class Impact:
    """What a changed control reaches."""

    control_id: str
    topics: list[str] = field(default_factory=list)
    documents: list[str] = field(default_factory=list)
    #: The subset of `documents` reached only by the shape of an 800-53 id
    #: in a citation that did not resolve (80's ruling on #418), printed as
    #: such so the citation gets fixed where it is written.
    by_shape: list[str] = field(default_factory=list)
    parameters: list[str] = field(default_factory=list)

    @property
    def reaches_anything(self) -> bool:
        return bool(self.topics or self.documents or self.parameters)


def _statement_diff(old: str, new: str, *, context: int = 1) -> str:
    """The changed lines of a statement, not the whole thing."""
    diff = difflib.unified_diff(
        (old or "").splitlines(),
        (new or "").splitlines(),
        lineterm="",
        n=context,
    )
    body = [line for line in diff if line[:1] in "+-" and not line.startswith(("+++", "---"))]
    shown = body[:6]
    if len(body) > 6:
        shown.append(f"    ... {len(body) - 6} more changed line(s)")
    return "\n".join(f"    {line}" for line in shown)


def _enhancement_ids(control) -> set[str]:
    return {e.enhancement_id for e in getattr(control, "enhancements", [])}


def _parameters_of(control) -> set[str]:
    from policyforge.parameters.ledger import extract_parameters

    return {p.key for p in extract_parameters([control])}


def diff_catalogs(old_controls, new_controls) -> list[ControlChange]:
    """Compare two loads of the same framework."""
    old_by_id = {c.control_id: c for c in old_controls}
    new_by_id = {c.control_id: c for c in new_controls}
    changes: list[ControlChange] = []

    for control_id in sorted(set(new_by_id) - set(old_by_id)):
        control = new_by_id[control_id]
        changes.append(ControlChange(control_id=control_id, kind=ADDED, title=control.title))

    for control_id in sorted(set(old_by_id) - set(new_by_id)):
        control = old_by_id[control_id]
        changes.append(ControlChange(control_id=control_id, kind=REMOVED, title=control.title))

    for control_id in sorted(set(old_by_id) & set(new_by_id)):
        old, new = old_by_id[control_id], new_by_id[control_id]
        fields: list[str] = []
        detail = ""

        if _normalize(old.control_statement) != _normalize(new.control_statement):
            fields.append("control_statement")
            detail = _statement_diff(old.control_statement, new.control_statement)
        if (old.baseline or "") != (new.baseline or ""):
            fields.append("baseline")
            detail = detail or f"    baseline: {old.baseline or 'none'} -> {new.baseline or 'none'}"
        if _enhancement_ids(old) != _enhancement_ids(new):
            fields.append("enhancements")
        if _parameters_of(old) != _parameters_of(new):
            fields.append("parameters")
        if _normalize(old.discussion) != _normalize(new.discussion):
            fields.append("discussion")
        if (old.title or "") != (new.title or ""):
            fields.append("title")
        if sorted(old.related_controls) != sorted(new.related_controls):
            fields.append("related_controls")

        if fields:
            changes.append(
                ControlChange(
                    control_id=control_id,
                    kind=CHANGED,
                    title=new.title,
                    fields=fields,
                    detail=detail,
                )
            )
        changes.extend(_enhancement_changes(old, new))

    return changes


def _enhancement_changes(old, new) -> list[ControlChange]:
    """Each enhancement of a control present in both loads that arrived, left,
    or is not what it was, under the ENHANCEMENT's own id (#369).

    **Compared this used to be by id alone**, so a reworded enhancement was
    never reported: measured on the shipped catalogs, 0 changes for 1,573
    enhancements across 8 catalogs, including every 800-53 enhancement, AI
    RMF subcategory and HIPAA implementation specification. The id a user
    cites, and the id impact has to reach (#339), is the enhancement's; the
    parent keeps `fields=["enhancements"]` for an add or remove, as a pointer
    (80's ruling on #369).

    **Fields compared** (named so a later narrowing is visible): the
    `description` (the requirement's text) and `title`, whitespace-normalised
    as `control_statement` is; the `baseline` (HIPAA's Required/Addressable);
    and the parameter ids. Not compared: `additional_requirements` and
    `source_crosswalk`, as at the control level.
    """
    old_by_id = {e.enhancement_id: e for e in getattr(old, "enhancements", [])}
    new_by_id = {e.enhancement_id: e for e in getattr(new, "enhancements", [])}
    found: list[ControlChange] = []
    for eid in sorted(set(new_by_id) - set(old_by_id)):
        found.append(ControlChange(control_id=eid, kind=ADDED, title=new_by_id[eid].title))
    for eid in sorted(set(old_by_id) - set(new_by_id)):
        found.append(ControlChange(control_id=eid, kind=REMOVED, title=old_by_id[eid].title))
    for eid in sorted(set(old_by_id) & set(new_by_id)):
        a, b = old_by_id[eid], new_by_id[eid]
        fields: list[str] = []
        detail = ""
        if _normalize(a.description) != _normalize(b.description):
            fields.append("description")
            detail = _statement_diff(a.description, b.description)
        if (a.baseline or "") != (b.baseline or ""):
            fields.append("baseline")
            detail = detail or f"    baseline: {a.baseline or 'none'} -> {b.baseline or 'none'}"
        if set(a.parameter_values or {}) != set(b.parameter_values or {}):
            fields.append("parameters")
        if _normalize(a.title) != _normalize(b.title):
            fields.append("title")
        if fields:
            found.append(
                ControlChange(
                    control_id=eid, kind=CHANGED, title=b.title, fields=fields, detail=detail
                )
            )
    return found


def _whole_family(control_id: str) -> str:
    """An 800-53 id's two-letter family (`AC` for `AC-2(3)`), the unit a
    source's family link names (#448)."""
    return control_id.split("-", 1)[0].strip().upper()


#: How a document was reached: through a citation that resolved, or only by
#: the shape of an 800-53 id in one that did not (80's ruling on #418).
RESOLVED = "resolved"
BY_SHAPE = "by id shape"


def documents_citing(
    controls: set[str], root: Path, *, framework: str = "", catalog_ids=(), crosswalk=None
) -> dict[str, list[str]]:
    """Control id -> the documents whose source tags cite its family.

    `documents_reached` without saying how; see it for the rule.
    """
    reached = documents_reached(
        controls, root, framework=framework, catalog_ids=catalog_ids, crosswalk=crosswalk
    )
    return {changed: sorted(paths) for changed, paths in reached.items()}


def documents_reached(
    controls: set[str],
    root: Path,
    *,
    framework: str = "",
    catalog_ids=(),
    crosswalk=None,
    catalogs: dict[str, set[str]] | None = None,
    families: dict[str, dict[str, str]] | None = None,
    family_links: dict[str, dict[str, frozenset[str]]] | None = None,
) -> dict[str, dict[str, str]]:
    """Control id -> {document: how it was reached, `RESOLVED` or `BY_SHAPE`}.

    **Through a family link, both ways** (80's ruling on #448, #377's hub
    rule being symmetric): `family_links` (`declared_family_links`) says a
    catalog's id stands for a whole 800-53 family (CSF 2.0's `GV.OC-03 ->
    PT`). A change to any control in that family reaches a document citing
    the id, and a change to the id reaches a document citing any control in
    the family. It never makes two 800-53 controls of one family reach each
    other: that stays #377's control-level rule.

    Walks the content tree rather than the corpus, because the question is
    which *files you maintain* need re-reading, and those are the ones under
    review in a pull request.

    **Document reach, the broader of the two breadths** (`topics.anchoring`,
    80's ruling on #377): a change reaches every document citing the same
    family **in the change's own framework**. A document citing AC-2(3) is
    reached by a change to AC-2(1), because a reader re-checking one part
    of AC-2 has to re-check the control; `Govern 1.3` reaches `Govern 1`
    and every `Govern 1.x`; a Playbook action reaches Playbook citations of
    its subcategory and never the Core's. Missing a document is the
    dangerous direction, so this reaches wider than topic ownership does.

    **Across catalogs, through the 800-53 hub** (80's ruling (ii) on #377):
    every citation stands for 800-53 families, its own if it cites 800-53,
    otherwise those the loaded crosswalk maps it to in full, and a change
    reaches a document whose citation shares one with it. `crosswalk` is
    `build_crosswalk` over the catalogs actually loaded.

    **Each citation is resolved before it is matched** (80's ruling on
    #418), because a citation `check` accepts is one a document really
    writes, and 1d measured four that the first version dropped in silence:

    - a framework-name variant (`[NIST SP 800-53 AC-2(3)]`, `[NIST 800-53
      Rev 5 AC-2(3)]`): the words the tag splitter left on the id are taken
      back into the name, longest first, while the rest still resolves;
    - a statement part (`AC-6(1)(a)`): trailing parts come off until the
      catalog has the id;
    - an id a catalog does not carry, in a catalog whose crosswalk maps
      every id to 800-53's same id (FedRAMP, ARC-AMPE, derived from the
      data, never listed): 800-53's own, as (B) reaches them;
    - an abbreviation naming two loaded catalogs (`NIST`): each of them
      through which this change reaches the cited id.

    **What still does not resolve is reached by 800-53 id shape, for
    documents only, and says so.** Every 800-53-shaped id in its text
    counts by its 800-53 family, the way every document was reached before
    #377, and the document is marked `BY_SHAPE` so drift prints it as
    reached by shape: partial success is allowed only when it is loud, and
    the citation is then fixed where it is written. Topic reach never uses
    this; ownership is not inferred from a shape.

    `catalogs` is `{catalog key: every id it has}` for the catalogs loaded,
    which is what "the catalog has the id" is asked of. Without it only the
    diffed catalog's ids and the crosswalk's are known.
    """
    from policyforge.content.tree import load_content_tree
    from policyforge.mapping.crosswalk import NIST_ANCHOR, normalize_framework
    from policyforge.topics.anchoring import (
        enclosing,
        family_of,
        hubs,
        is_800_53_shaped,
        regulatory_families,
        reverse_index,
        shaped_ids,
    )
    from policyforge.topics.satisfies import parse_citations, resolve_framework

    found: dict[str, dict[str, str]] = {}
    key = normalize_framework(framework) if framework else ""
    if not root.exists() or not key:
        return found
    reverse = reverse_index(crosswalk)
    # The catalogs a citation may name, and the ids each is known to have.
    index: dict[str, set[str]] = {k: set(v) for k, v in (catalogs or {}).items()}
    index.setdefault(key, set()).update(catalog_ids, controls)
    index.setdefault(NIST_ANCHOR, set()).update(crosswalk or {})
    for catalog, requirement_id in reverse:
        index.setdefault(catalog, set()).add(requirement_id)
    identity = {
        catalog
        for catalog in {c for c, _ in reverse}
        if catalog != NIST_ANCHOR
        and all(ids == {rid} for (c, rid), ids in reverse.items() if c == catalog)
    }

    declared = families or {}

    def fam(requirement_id: str, catalog: str) -> set[str]:
        """Its families: by the catalog's declared unit where it declares
        one (a regulatory catalog, 80's ruling on #423), else its grammar."""
        if catalog in declared:
            return regulatory_families(requirement_id, declared[catalog])
        return {family_of(requirement_id, catalog)}

    own: dict[str, set[str]] = {}
    hub: dict[str, set[str]] = {}
    links = family_links or {}
    # By 800-53 family (`AC`): changed 800-53 controls, for citations of a
    # linked id; and changed linked ids, for citations of 800-53 controls.
    in_linked_family: dict[str, set[str]] = {}
    linked_to_family: dict[str, set[str]] = {}
    for changed in controls:
        for family in fam(changed, key):
            own.setdefault(family, set()).add(changed)
        for family in hubs(changed, framework, reverse):
            hub.setdefault(family, set()).add(changed)
        if key == NIST_ANCHOR or key in identity:
            in_linked_family.setdefault(_whole_family(changed), set()).add(changed)
        for whole in links.get(key, {}).get(changed, ()):
            linked_to_family.setdefault(whole, set()).add(changed)

    def known(catalog: str, requirement_id: str) -> bool:
        # A standard cited by its paragraph is not "known" here and needs not
        # be: it falls to the name-as-written reading below, and `reached`
        # resolves it through `fam` (an arm on #423 showed no input tells
        # the two apart).
        return requirement_id in index.get(catalog, ())

    def reached(cited: str, requirement_id: str) -> set[str]:
        hit = set()
        if cited == key:
            for family in fam(requirement_id, cited):
                hit |= own.get(family, set())
        # A cited ancestor has no crosswalk of its own: its standards do.
        stands_for = {requirement_id} | (fam(requirement_id, cited) if cited in declared else set())
        hub_families = set().union(*(hubs(i, cited, reverse) for i in stands_for))
        if not hub_families and cited in identity:
            hub_families = {family_of(requirement_id, NIST_ANCHOR)}
        for family in hub_families:
            hit |= hub.get(family, set())
        # Family links, both ways (#448).
        if cited == NIST_ANCHOR or cited in identity:
            hit |= linked_to_family.get(_whole_family(requirement_id), set())
        for whole in links.get(cited, {}).get(requirement_id, ()):
            hit |= in_linked_family.get(whole, set())
        return hit

    def resolutions(written: str, requirement_id: str) -> list[tuple[str, str]]:
        """(catalog, id) readings of one citation that name a loaded catalog."""
        tokens = requirement_id.split()
        # Longest name first: `NIST 800-53 Rev 5 AC-2(3)` arrives as
        # ("NIST 800-53", "Rev 5 AC-2(3)"), and "Rev" alone would leave "5".
        for taken in range(len(tokens) - 1, -1, -1):
            name = " ".join([written, *tokens[:taken]])
            rest = " ".join(tokens[taken:])
            cited = resolve_framework(name, index) or (
                normalize_framework(name) if normalize_framework(name) in index else ""
            )
            candidates = (
                [cited]
                if cited
                else [c for c, ids in index.items() if resolve_framework(name, {c: ids}) == c]
            )
            readings = [
                (c, enclosing(rest, lambda r, c=c: known(c, r)))
                for c in candidates
                if known(c, enclosing(rest, lambda r, c=c: known(c, r)))
                or (c in identity and is_800_53_shaped(rest))
            ]
            if readings:
                return readings
        # Nothing the catalogs have: the name as written, if it names one.
        cited = resolve_framework(written, index)
        return [(cited, requirement_id)] if cited else []

    documents, _ = load_content_tree(root)
    for document in documents:
        for written, requirement_id, _, _ in parse_citations(document.body, [framework], index):
            readings = resolutions(written, requirement_id)
            hits = set()
            for cited, rid in readings:
                hits |= reached(cited, rid)
            how = RESOLVED
            if not hits and not any(known(c, r) for c, r in readings):
                # Unresolved: 800-53 shape, documents only, marked.
                for shaped in shaped_ids(f"{written} {requirement_id}"):
                    hits |= hub.get(family_of(shaped, NIST_ANCHOR), set())
                    if key == NIST_ANCHOR:
                        hits |= own.get(family_of(shaped, NIST_ANCHOR), set())
                how = BY_SHAPE
            for changed in hits:
                paths = found.setdefault(changed, {})
                if paths.get(document.relative_path) != RESOLVED:
                    paths[document.relative_path] = how
    return found


def assess_impact(
    changes,
    *,
    topics=(),
    content_root=None,
    decisions=None,
    framework: str = "",
    catalog_ids=(),
    crosswalk=None,
    catalogs=None,
    families=None,
    family_links=None,
) -> dict[str, Impact]:
    """Work out what each changed control reaches.

    `framework` is the catalog the changes are in. Topic reach is the one
    rule, `topics.anchoring.topic_keys` (#377): the change's own catalog's
    parent for one topics anchor, and through `crosswalk` for any other, so
    a Playbook change reaches no topic and an 800-171 change reaches the
    topics owning the 800-53 controls NIST maps it to.
    """
    from policyforge.topics.anchoring import topic_keys

    changed_ids = {c.control_id for c in changes}
    impacts = {control_id: Impact(control_id=control_id) for control_id in changed_ids}

    for topic in topics or ():
        anchors = {a.upper() for a in getattr(topic, "nist_controls", [])}
        for control_id in changed_ids:
            keys = {k.upper() for k in topic_keys(control_id, framework, crosswalk)}
            if keys & anchors:
                impacts[control_id].topics.append(topic.name)

    if content_root is not None:
        reached = documents_reached(
            changed_ids,
            Path(content_root),
            framework=framework,
            catalog_ids=catalog_ids,
            crosswalk=crosswalk,
            catalogs=catalogs,
            families=families,
            family_links=family_links,
        )
        for control_id, paths in reached.items():
            impacts[control_id].documents.extend(sorted(paths))
            impacts[control_id].by_shape.extend(
                sorted(p for p, how in paths.items() if how == BY_SHAPE)
            )

    for key in decisions or {}:
        control_id = key.split("/")[0].upper()
        for changed in changed_ids:
            if changed.upper() == control_id:
                impacts[changed].parameters.append(key)

    return impacts


@dataclass
class DriftReport:
    old_version: str = ""
    new_version: str = ""
    changes: list[ControlChange] = field(default_factory=list)
    impacts: dict[str, Impact] = field(default_factory=dict)

    @property
    def substantive(self) -> list[ControlChange]:
        return [c for c in self.changes if c.substantive]

    @property
    def editorial(self) -> list[ControlChange]:
        return [c for c in self.changes if not c.substantive]

    @property
    def affected_topics(self) -> list[str]:
        return sorted({t for c in self.substantive for t in self.impacts[c.control_id].topics})

    @property
    def affected_documents(self) -> list[str]:
        return sorted({d for c in self.substantive for d in self.impacts[c.control_id].documents})

    @property
    def affected_parameters(self) -> list[str]:
        return sorted({p for c in self.substantive for p in self.impacts[c.control_id].parameters})

    @property
    def needs_review(self) -> bool:
        return bool(self.substantive)

    def format_report(self, *, limit: int = 25, detail: bool = False) -> str:
        version = f"{self.old_version or 'previous'} -> {self.new_version or 'current'}"
        if not self.changes:
            return f"No change between {version}. Nothing to review."

        added = [c for c in self.changes if c.kind == ADDED]
        removed = [c for c in self.changes if c.kind == REMOVED]
        changed = [c for c in self.changes if c.kind == CHANGED]
        lines = [
            f"{version}: {len(added)} added, {len(removed)} removed, {len(changed)} changed.",
            f"{len(self.substantive)} change(s) alter what the organization must do; "
            f"{len(self.editorial)} are editorial.",
        ]

        if self.substantive:
            lines += ["", "Worth reading:"]
            for change in self.substantive[:limit]:
                what = ", ".join(change.fields) or change.kind
                impact = self.impacts.get(change.control_id, Impact(change.control_id))
                reach = []
                if impact.topics:
                    reach.append(f"topics: {', '.join(impact.topics)}")
                if impact.documents:
                    reach.append(
                        "docs: "
                        + ", ".join(
                            f"{d} (reached by id shape; citation did not resolve)"
                            if d in impact.by_shape
                            else d
                            for d in impact.documents
                        )
                    )
                if impact.parameters:
                    reach.append(f"parameters: {', '.join(impact.parameters)}")
                lines.append(f"  {change.kind.upper():7} {change.control_id}  ({what})")
                if reach:
                    lines.append(f"          {' | '.join(reach)}")
                elif change.kind != REMOVED:
                    lines.append("          reaches nothing you have written yet")
                if detail and change.detail:
                    lines.append(change.detail)
            if len(self.substantive) > limit:
                lines.append(f"  ... and {len(self.substantive) - limit} more")

        if self.editorial:
            lines += [
                "",
                f"Editorial only ({len(self.editorial)}): "
                + ", ".join(c.control_id for c in self.editorial[:limit])
                + (" ..." if len(self.editorial) > limit else ""),
            ]

        lines += ["", "Blast radius:"]
        lines.append(
            f"  {len(self.affected_topics)} topic(s): "
            + (", ".join(self.affected_topics) or "none")
        )
        lines.append(
            f"  {len(self.affected_documents)} document(s): "
            + (", ".join(self.affected_documents) or "none")
        )
        lines.append(
            f"  {len(self.affected_parameters)} recorded parameter decision(s): "
            + (", ".join(self.affected_parameters) or "none")
        )
        shaped = sorted({d for c in self.substantive for d in self.impacts[c.control_id].by_shape})
        if shaped:
            lines += [
                "",
                f"{len(shaped)} of those document(s) were reached only by the shape of an "
                "800-53 id, because a citation in them did not resolve to any loaded "
                "catalog: " + ", ".join(shaped) + ". Fix the citation where it is "
                "written; until then drift can only guess what it means.",
            ]
        if self.affected_documents:
            lines += [
                "",
                "Re-read those documents against the new control text. Regenerating "
                "the whole set would discard every hand edit and every review it has "
                "ever had, which is the more expensive mistake.",
            ]
        return "\n".join(lines)


def read_committed(path: Path, *, revision: str = "HEAD") -> str | None:
    """The committed contents of a tracked file, or None.

    This is what makes "run the ETL, then see what changed" work without
    anybody having to snapshot anything first: the ETL overwrites the
    catalog in place and git is still holding the version you had.
    """
    from policyforge.child_output import strict_text

    argv = ["git", "show", f"{revision}:{Path(path).as_posix()}"]
    try:
        result = subprocess.run(argv, capture_output=True, timeout=60, check=False)  # nosec B603 B607
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    # DATA: parsed as the committed catalog a drift is measured against.
    # Before #287 a committed `café` read back as `cafÃ©`, and a byte cp1252
    # leaves undefined crashed with an AttributeError on None.
    text = strict_text(result.stdout, site="drift (committed catalog)", argv=argv)
    return text if text.strip() else None


def _controls_from_json(text: str):
    from policyforge.ingest.schema import Control, ControlEnhancement, parse_catalog

    # Through the one catalog rule: a committed licensed catalog is an
    # envelope since #459 (b), and `json.loads` alone raised on it here,
    # taking `drift` and the shell's drift skill down for every catalog.
    rows, _ = parse_catalog(text, source="the committed catalog")
    controls = []
    for item in rows:
        item = dict(item)
        enhancements = [ControlEnhancement(**e) for e in item.pop("enhancements", [])]
        controls.append(Control(enhancements=enhancements, **item))
    return controls


def load_previous(path: Path, *, revision: str = "HEAD"):
    """The previous version of a catalog, from git."""
    text = read_committed(path, revision=revision)
    return _controls_from_json(text) if text else None


def _catalog_ids(controls) -> set[str]:
    """Every control and enhancement id in a catalog."""
    return {c.control_id for c in controls} | {
        e.enhancement_id for c in controls for e in c.enhancements
    }


def analyze_drift(
    old_controls,
    new_controls,
    *,
    topics=(),
    content_root=None,
    decisions=None,
    crosswalk=None,
    catalogs=None,
    families=None,
    family_links=None,
) -> DriftReport:
    """`crosswalk` is `build_crosswalk` over every catalog loaded (the drift
    command passes the installation's); by default, the diffed catalog's
    own two versions, so a call with nothing else loaded reaches only
    through the mapping that catalog carries."""
    from policyforge.mapping.crosswalk import build_crosswalk

    if crosswalk is None:
        crosswalk = build_crosswalk([*(old_controls or []), *(new_controls or [])])
    if families is None:
        # The diffed catalog's declared unit, if it has one (#423).
        from policyforge.topics.anchoring import families_for

        families = families_for([*(old_controls or []), *(new_controls or [])])
    if family_links is None:
        # The manifests' links to whole families (#448), as `families` above.
        from policyforge.frameworks.registry import config_or_defaults, declared_family_links

        family_links = declared_family_links(config_or_defaults("family links were read"))
    changes = diff_catalogs(old_controls, new_controls)
    return DriftReport(
        old_version=(old_controls[0].framework_version if old_controls else ""),
        new_version=(new_controls[0].framework_version if new_controls else ""),
        changes=changes,
        impacts=assess_impact(
            changes,
            topics=topics,
            content_root=content_root,
            decisions=decisions,
            framework=(new_controls or old_controls or [None])[0].framework
            if (new_controls or old_controls)
            else "",
            catalog_ids=_catalog_ids(old_controls or []) | _catalog_ids(new_controls or []),
            crosswalk=crosswalk,
            catalogs=catalogs,
            families=families,
            family_links=family_links,
        ),
    )
