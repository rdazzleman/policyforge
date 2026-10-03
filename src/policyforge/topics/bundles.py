"""The two questions people actually ask, answered from data already held.

`coverage.py` answers the programme-wide question — how much of a baseline
has an owner — which is the question the person running the programme asks.
Two other people ask narrower questions constantly, and neither was
answerable without reading the whole coverage report and filtering it by
hand:

* **A team lead: "what am I accountable for?"** Not the 300-row table. The
  topics their team owns, the requirements those topics answer for, the
  documents they are expected to keep current, and the cadence they are
  expected to keep them on.
* **An assessor: "show me where you address this."** They name a
  requirement — often not a NIST one — and want the document. Today that
  means reading the registry, following the crosswalk by hand, and
  guessing which page covers it.

Both are set arithmetic over the registry, the catalogs and the crosswalk.
No model is involved, nothing here can be wrong in an interesting way, and
the answers are exactly as good as the registry is.

**Every claim says how it was reached.** That is the part worth care. A
requirement can be owned because a topic anchored it directly, because its
parent control was anchored and enhancements are inherited, or because it is
reachable through the crosswalk from an anchored NIST control. Those are
three different strengths of claim, and collapsing them would let a topic
that anchored AC-2 appear to have deliberately addressed a HITRUST
requirement nobody has ever read. An assessor is entitled to know which of
the three they are being shown, so `Route` travels with every answer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from policyforge.mapping.crosswalk import normalize_framework
from policyforge.topics.anchoring import anchor_keys
from policyforge.topics.registry import Topic

#: Anchored by name. The strongest claim: somebody wrote this control id
#: into the registry against this topic.
DIRECT = "direct"
#: Claimed because the parent control was anchored. Real, and weaker —
#: nobody named this enhancement, they named the control it hangs off.
INHERITED = "inherited"
#: Reached from an anchored NIST control through the framework crosswalk.
#: The weakest: it says the requirements are equivalent according to a
#: published mapping, not that anyone wrote this requirement down.
CROSSWALKED = "crosswalked"

#: How each route should be described to somebody who did not build this.
ROUTE_NOTES = {
    DIRECT: "anchored directly by this topic",
    INHERITED: (
        "inherited — the parent is anchored, and anchoring an item claims what "
        "sits beneath it: a control's enhancements, or an AI RMF category's "
        "subcategories"
    ),
    CROSSWALKED: (
        "reached through the published crosswalk from an anchored NIST control, "
        "not written down as this requirement"
    ),
}


@dataclass
class OwnedRequirement:
    """One requirement a topic answers for, and how strongly."""

    requirement_id: str
    topic: str
    owner: str
    route: str = DIRECT
    #: For a crosswalked requirement, the NIST control it was reached from.
    #: Empty otherwise. Carried because "we cover 164.308(a)(1)(i) via RA-1"
    #: is checkable and "we cover it" is not.
    via: str = ""

    @property
    def note(self) -> str:
        return ROUTE_NOTES.get(self.route, "")


@dataclass
class TeamBundle:
    """Everything one owner answers for."""

    owner: str
    topics: list[Topic] = field(default_factory=list)
    requirements: list[OwnedRequirement] = field(default_factory=list)
    #: framework -> requirement ids this owner's topics reach.
    frameworks: dict[str, list[str]] = field(default_factory=dict)
    #: Every owner the registry does name, for the case where this one is
    #: not among them. Carried on the bundle rather than looked up by the
    #: caller so that both callers — the CLI and the shell — answer the
    #: same way.
    known_owners: list[str] = field(default_factory=list)

    @property
    def exists(self) -> bool:
        """Whether any topic names this owner.

        A bundle for an owner nobody has heard of is empty, and that is a
        different fact from an owner who owns topics with no controls.
        """
        return bool(self.topics)

    @property
    def documents(self) -> list[tuple[str, str, str]]:
        """(topic, tier, page title) for every published document owned."""
        found = []
        for topic in self.topics:
            for tier, title in topic.confluence_pages():
                found.append((topic.name, tier, title))
        return found

    @property
    def undocumented_topics(self) -> list[str]:
        """Topics with no published pages.

        Worth naming separately: a topic with requirements and no document
        is a commitment nobody can read, and it is invisible in a coverage
        report because coverage counts controls, not pages.
        """
        return sorted(t.name for t in self.topics if not t.confluence_pages())

    def render(self) -> str:
        if not self.exists:
            # The owners are listed rather than a command named. This
            # message is rendered by both the CLI's `bundle` and the
            # shell's `/team`, so any command it named would be wrong in
            # one of them — and the previous text named `policyforge
            # topics`, which exists in neither. An answer beats a remedy.
            if self.known_owners:
                owners = "\n".join(f"  {name}" for name in self.known_owners)
                return (
                    f"No topic in the registry is owned by {self.owner!r}. "
                    f"The registry names these owners:\n{owners}\n"
                    f"A freshly discovered registry assigns every topic to "
                    f"[UNASSIGNED] until somebody edits it."
                )
            return (
                f"No topic in the registry is owned by {self.owner!r}, and the "
                f"registry names no owners at all — it is empty, or every topic "
                f"is still [UNASSIGNED]."
            )

        direct = [r for r in self.requirements if r.route == DIRECT]
        inherited = [r for r in self.requirements if r.route == INHERITED]

        lines = [
            f"{self.owner} owns {len(self.topics)} topic(s) "
            f"and answers for {len(self.requirements)} requirement(s).",
            "",
        ]
        for topic in self.topics:
            owned = [r for r in self.requirements if r.topic == topic.name]
            pages = topic.confluence_pages()
            lines.append(f"  {topic.name} — {len(owned)} requirement(s)")
            if topic.cadence:
                lines.append(f"      review cadence: {topic.cadence}")
            if pages:
                for tier, title in pages:
                    lines.append(f"      {tier}: {title}")
            else:
                lines.append("      no published documents")
        lines += [
            "",
            f"{len(direct)} anchored directly, {len(inherited)} inherited from an anchored "
            "parent (a control, or an AI RMF category).",
        ]

        if self.frameworks:
            lines += ["", "Reachable through the crosswalk:"]
            for framework, ids in sorted(self.frameworks.items()):
                lines.append(f"  {framework}: {len(ids)} requirement(s)")
            lines.append(
                "  These are equivalences from a published mapping, not requirements "
                "anybody wrote against. Read them as leads, not as coverage."
            )

        if self.undocumented_topics:
            lines += [
                "",
                f"{len(self.undocumented_topics)} topic(s) with no published document: "
                + ", ".join(self.undocumented_topics),
                "  A requirement owned with nothing written down is a commitment nobody can read.",
            ]
        return "\n".join(lines)


@dataclass
class RequirementView:
    """Who answers for one requirement, and where it is written down."""

    requirement_id: str
    claims: list[OwnedRequirement] = field(default_factory=list)
    #: (topic, tier, page title) for every document that should carry it.
    documents: list[tuple[str, str, str]] = field(default_factory=list)
    #: True when the id matched nothing in any catalog — a typo, or a
    #: framework that was never loaded. Different from "nobody owns it".
    unknown: bool = False

    @property
    def owned(self) -> bool:
        return bool(self.claims)

    @property
    def contested(self) -> bool:
        return len({c.topic for c in self.claims}) > 1

    def render(self) -> str:
        if self.unknown:
            return (
                f"{self.requirement_id} is not in any loaded catalog. Either it is "
                f"mistyped, or the framework it belongs to has not been loaded — "
                f"`policyforge coverage` lists which catalogs are in play."
            )
        if not self.owned:
            return (
                f"Nothing in the registry answers for {self.requirement_id}. "
                f"That is a real gap, not a lookup failure: no topic anchors it, "
                f"and no topic anchors a control the crosswalk maps it to."
            )

        lines = [f"{self.requirement_id}"]
        for claim in self.claims:
            via = f" via {claim.via}" if claim.via else ""
            lines.append(f"  {claim.topic} — owned by {claim.owner}{via}")
            lines.append(f"      {claim.note}")
        if self.documents:
            lines += ["", "Written down in:"]
            lines += [f"  {tier}: {title}  ({topic})" for topic, tier, title in self.documents]
        else:
            lines += [
                "",
                "No published document. The requirement has an owner and nothing "
                "an assessor can be shown.",
            ]
        if self.contested:
            lines += [
                "",
                "More than one topic claims this. That is a registry problem — "
                "two teams each believing the other has it is how a requirement "
                "goes unmet.",
            ]
        return "\n".join(lines)


def _requirement_ids(controls) -> list[str]:
    return list(_frameworks_of(controls))


def _frameworks_of(controls) -> dict[str, str]:
    """{requirement id: its catalog's framework}, in catalog order. The parent
    rule reads the framework (#377); the ids of the catalogs topics anchor do
    not collide, so one map serves them all."""
    frameworks: dict[str, str] = {}
    for control in controls:
        frameworks[control.control_id] = control.framework
        for enhancement in control.enhancements:
            frameworks[enhancement.enhancement_id] = control.framework
    return frameworks


def _claims_for(
    topics: list[Topic],
    requirement_id: str,
    *,
    catalog: dict[str, str],
) -> list[OwnedRequirement]:
    """Direct and inherited claims on one NIST requirement.

    A direct claim beats an inherited one, matching `coverage.py`: a
    specifically-anchored enhancement can sit with a different team than its
    parent without either being reported as contested.
    """
    keys = anchor_keys(requirement_id, catalog.get(requirement_id, ""))
    direct, inherited = [], []
    for topic in topics:
        anchors = [a for a in topic.nist_controls if a in catalog]
        if requirement_id in anchors:
            direct.append(OwnedRequirement(requirement_id, topic.name, topic.owner, route=DIRECT))
        elif keys & set(anchors):
            inherited.append(
                OwnedRequirement(requirement_id, topic.name, topic.owner, route=INHERITED)
            )
    return direct or inherited


def team_bundle(
    topics: list[Topic],
    nist_controls: list,
    owner: str,
    *,
    crosswalk: dict[str, dict[str, list[str]]] | None = None,
) -> TeamBundle:
    """Everything `owner` answers for, as one artifact.

    Owner matching is exact but case-insensitive and whitespace-tolerant.
    Not fuzzy: a bundle is the thing a team lead acts on, and quietly
    matching "Security" to "Security Engineering" would hand somebody
    another team's obligations.
    """
    wanted = owner.strip().casefold()
    mine = [t for t in topics if t.owner.strip().casefold() == wanted]
    bundle = TeamBundle(
        owner=owner.strip(),
        topics=sorted(mine, key=lambda t: t.name),
        known_owners=sorted({t.owner.strip() for t in topics if t.owner.strip()}),
    )
    if not mine:
        return bundle

    catalog = _frameworks_of(nist_controls)
    for requirement_id in catalog:
        for claim in _claims_for(mine, requirement_id, catalog=catalog):
            bundle.requirements.append(claim)

    if crosswalk:
        owned_ids = {r.requirement_id for r in bundle.requirements}
        reachable: dict[str, set[str]] = {}
        for nist_id in owned_ids:
            for framework, equivalent in crosswalk.get(nist_id, {}).items():
                reachable.setdefault(normalize_framework(framework), set()).update(equivalent)
        bundle.frameworks = {f: sorted(ids) for f, ids in sorted(reachable.items())}

    return bundle


def requirement_view(
    topics: list[Topic],
    nist_controls: list,
    requirement_id: str,
    *,
    other_controls: list | None = None,
    crosswalk: dict[str, dict[str, list[str]]] | None = None,
) -> RequirementView:
    """Who answers for `requirement_id`, whichever framework it belongs to.

    The assessor's direction of travel. They name a HIPAA or HITRUST
    citation; this resolves it through the crosswalk to the NIST controls
    the registry is anchored on, and reports the topic, the owner and the
    pages — saying, for each, whether the claim is direct, inherited or
    merely crosswalked.
    """
    wanted = requirement_id.strip()
    view = RequirementView(requirement_id=wanted)
    catalog = _frameworks_of(nist_controls)

    if wanted in catalog:
        view.claims = _claims_for(topics, wanted, catalog=catalog)
    else:
        other_ids = set(_requirement_ids(other_controls or []))
        if wanted not in other_ids:
            view.unknown = True
            return view
        # A non-NIST requirement: find the NIST controls it is equivalent to,
        # then ask who owns those. The crosswalk is keyed NIST-first, so this
        # is a reverse lookup and every hit is explicitly marked as such.
        for nist_id, mapped in (crosswalk or {}).items():
            for equivalent in mapped.values():
                if wanted in equivalent and nist_id in catalog:
                    for claim in _claims_for(topics, nist_id, catalog=catalog):
                        view.claims.append(
                            OwnedRequirement(
                                requirement_id=wanted,
                                topic=claim.topic,
                                owner=claim.owner,
                                route=CROSSWALKED,
                                via=nist_id,
                            )
                        )
                    break

    by_topic = {t.name: t for t in topics}
    seen: set[tuple[str, str, str]] = set()
    for claim in view.claims:
        topic = by_topic.get(claim.topic)
        if topic is None:
            continue
        for tier, title in topic.confluence_pages():
            entry = (topic.name, tier, title)
            if entry not in seen:
                seen.add(entry)
                view.documents.append(entry)
    return view
