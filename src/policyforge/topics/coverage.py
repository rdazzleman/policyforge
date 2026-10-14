"""Coverage and ownership analysis over the topic registry.

Answers the two questions that sink a compliance program, both of which are
set arithmetic once topics are declared — no LLM involved:

* **Orphaned** — an in-scope control no topic claims. Nobody is doing it, and
  nobody knows nobody is doing it.
* **Contested** — an in-scope control two or more topics claim. This is the
  worse failure of the two: on paper it looks covered, while each owner
  assumes the other has it. Ambiguous ownership is precisely what the "one
  topic, one team" model exists to prevent, so it should be a report you run
  rather than a discovery you make during an assessment.

**Claim resolution.** Anchoring a control claims that control *and* its
enhancements, so a topic needn't enumerate AC-2(1)..AC-2(13) to own AC-2. A
topic may also anchor an enhancement directly, and a direct claim beats an
inherited one — that's how AC-2(1) can sit with a different team than AC-2
without either becoming contested.

**Other frameworks come along for free.** Topics anchor NIST controls, but
the crosswalk means a claimed NIST control also accounts for the HIPAA (or
FedRAMP, or HITRUST) requirements mapped to it. `framework_coverage`
reports, per framework, which requirements are reachable from some topic and
which aren't — the same orphan question asked from the assessor's side.

**Reached is not the same as covered.** Where an organization's crosswalk
overlay records how a pair relates, a requirement reached only through
controls recorded as `superset` (the requirement asks for more than the
control does) or `intersects` is reported apart, as reached in part. It may
be fully met by several such controls together, and it may not; no single
owned control is recorded as covering it, and that is a question for a
person, not a count to fold into "covered". A pair with no recorded
relationship counts as reaching the requirement, which is what every pair
did before relationships were recorded.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from policyforge.frameworks.vacated import VACATED
from policyforge.ingest.schema import MATURITY, Control, Requirement
from policyforge.mapping.crosswalk import normalize_framework
from policyforge.topics.anchoring import parent_of
from policyforge.topics.registry import Topic

#: A link its source published untyped and declared incomplete; see
#: `crosswalk.overlay.RELATIONSHIPS` for its meaning (#408).
SOURCE_UNTYPED = "source-untyped"
#: Relationships under which one control covers only part of a requirement.
PARTIAL_RELATIONSHIPS = frozenset({"superset", "intersects", SOURCE_UNTYPED})


def reaches_in_full(
    framework: str,
    requirement_id: str,
    nist_id: str,
    relationships: dict[tuple[str, str, str], str],
) -> bool:
    """Whether owning `nist_id` accounts for all of `requirement_id`.

    **The one reading of "partial"** (#185; 80's condition on #377). A pair
    with no recorded relationship reaches in full, which is what every pair
    did before relationships were recorded; one in `PARTIAL_RELATIONSHIPS`
    (`superset` or `intersects` from a reviewer, `source-untyped` from a
    catalog's manifest, #408) reaches only in part, and whether that is enough
    is a person's call. `/coverage` and every reach through the crosswalk ask
    this, so none of them reads the relationship table a second way.
    """
    return relationships.get((framework, requirement_id, nist_id)) not in PARTIAL_RELATIONSHIPS


@dataclass
class FrameworkCoverage:
    """How much of one non-NIST framework the topics reach, via the crosswalk."""

    framework: str
    covered: list[str] = field(default_factory=list)
    uncovered: list[str] = field(default_factory=list)
    #: Reached only through owned controls recorded as covering part of the
    #: requirement: a relationship in `PARTIAL_RELATIONSHIPS` (`superset`,
    #: `intersects`, or a catalog's declared `source-untyped`).
    partial: list[str] = field(default_factory=list)
    #: True when the framework is counted per LEVEL requirement ("01.a
    #: Level 1") rather than per control -- HITRUST, whose crosswalk is
    #: published per level (#282). The report says so, because the unit is
    #: what the number means.
    per_level: bool = False
    #: When the framework's published links are `source-untyped` (#408): the
    #: requirements its source links to no 800-53 control at all. The source
    #: declares its mapping incomplete, so these are "not mapped by the
    #: source", never "no 800-53 equivalent". None for every other framework.
    unmapped_by_source: list[str] | None = None
    #: Who published that mapping, as the catalog's manifest names it
    #: (`crosswalk_source:`), for the report's wording. Empty when undeclared.
    source_name: str = ""
    #: Its source's links to WHOLE 800-53 families (#448): {its id: the
    #: families}. Named in the report, never counted: a family link covers no
    #: control (80's ruling on #408). Empty for a framework with none.
    family_links: dict[str, list[str]] = field(default_factory=dict)
    #: For a per-level framework: the level scoping it was counted under, as
    #: the report prints it (#283), or "" when none is declared and every
    #: level is counted.
    scoping: str = ""
    #: {requirement counted: why}, where a reference had no statement at the
    #: declared maturity level and a lower one stood in (80's ruling on #283).
    stood_in: dict[str, str] = field(default_factory=dict)
    #: {kind: [(requirement not counted, why)]}: every requirement a declared
    #: scope leaves out, so that `total` plus these is the catalog's whole
    #: requirement count (80's ruling on #5: anything scoped out is named).
    #: Pairs, not a mapping: two requirements can share an id (a real
    #: export has six), and each is one requirement to account for.
    #: Empty where no scope is declared, since then every level is counted.
    scoped_out: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    #: HITRUST only: {level label: requirements at it} for every label at the
    #: report's 36-character level-name width (#27). Whether each was cut
    #: can't be told from the catalog, since a real name can be that long;
    #: named here so a user declaring a level sees the name as it is written
    #: (80's ruling on #27, comment 415). Empty for every other framework.
    levels_at_cut_width: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return len(self.covered) + len(self.partial) + len(self.uncovered)


# ---- scoping a per-level framework to the levels that apply (#283) ----------


class ScopingError(ValueError):
    """`frameworks.scoping` in config cannot be applied as written (#283): an
    unknown overlay, a maturity that is not a level, or a key naming no loaded
    per-level framework. A `ValueError`, so existing handlers still catch it;
    its own type so the CLI and the shell can show it as a refusal, with its
    message, and not as a traceback (1d on #461)."""


@dataclass(frozen=True)
class LevelScope:
    """Which levels of a per-level framework apply to this organisation, as
    its config declares them (80's ruling on #283). Organisation-wide: the
    levels follow from its own scoping answers, not from a topic.

    `maturity` is the level it is assessed at: per reference, the highest
    maturity statement AT OR BELOW it is counted, since the maturity levels
    are alternatives. `overlays` are switched on by name and are additive.
    `maturity` None means undeclared, so every maturity level is counted."""

    maturity: int | None = None
    overlays: tuple[str, ...] = ()


def level_scopes(config: dict | None = None) -> dict[str, LevelScope]:
    """`frameworks.scoping` in config: {framework key: its LevelScope}.

    For example, `frameworks: {scoping: {hitrust: {maturity: 2, overlays: [...]}}}`.
    A maturity that is not a positive integer is refused, naming it."""
    from policyforge.frameworks.registry import frameworks_config

    scopes: dict[str, LevelScope] = {}
    for name, block in (frameworks_config(config).get("scoping") or {}).items():
        block = block or {}
        maturity = block.get("maturity")
        if maturity is not None and (
            isinstance(maturity, bool) or not isinstance(maturity, int) or maturity < 1
        ):
            raise ScopingError(
                f"frameworks.scoping.{name}.maturity must be a level number (1, 2, 3 ...), "
                f"not {maturity!r}."
            )
        overlays = tuple(str(o) for o in (block.get("overlays") or ()))
        scopes[normalize_framework(str(name))] = LevelScope(maturity, overlays)
    return scopes


def _label_key(label: str) -> str:
    """A level label as matching reads it (80's ruling on #283): whitespace
    normalised, case folded, and a leading "Level" dropped, so a declared
    `hipaa` matches the export's `Level  HIPAA`."""
    words = label.split()
    if words and words[0].casefold() == "level":
        words = words[1:]
    return " ".join(words).casefold()


def _maturity_number(label: str) -> int | None:
    key = _label_key(label)
    return int(key) if key.isdigit() else None


#: Why a declared scope leaves a requirement out, as `FrameworkCoverage.
#: scoped_out` keys them and the report prints them, in this order. The
#: first is the one that shrinks the population of REFERENCES (1d on #461).
NO_STATEMENT_AT_OR_BELOW = "reference not counted: no statement at or below the declared level"
ABOVE_DECLARED = "above the declared maturity level"
SUPERSEDED = "a higher statement at or below the declared level is counted instead"
UNNUMBERED = "maturity label is not a level number"
OVERLAY_NOT_DECLARED = "overlay not declared"
SCOPED_OUT_KINDS = (
    NO_STATEMENT_AT_OR_BELOW,
    ABOVE_DECLARED,
    SUPERSEDED,
    UNNUMBERED,
    OVERLAY_NOT_DECLARED,
)


def _scoped_requirements(
    controls: list[Control], framework: str, scope: LevelScope
) -> tuple[list[str], dict[str, str], dict[str, list[tuple[str, str]]]]:
    """The requirement ids of `controls` that `scope` counts; the ones
    counted at a lower maturity level than declared, with why; and every
    other requirement, by the kind of reason it is not counted, with why.

    Each requirement lands in exactly one of `counted` and the scoped-out
    kinds, so the two together are the catalog's whole requirement set:
    nothing a scope leaves out goes unnamed (80's ruling on #5).

    Overlays are matched against the catalog's OWN label set; one that
    matches none is refused, naming the nearest labels it has, because a
    misspelt overlay would otherwise count nothing, silently."""
    import difflib

    overlay_labels = {
        _label_key(r.level): r.level
        for c in controls
        for r in c.requirements
        if r.level_kind != MATURITY
    }
    wanted: set[str] = set()
    for declared in scope.overlays:
        key = _label_key(declared)
        if key not in overlay_labels:
            nearest = difflib.get_close_matches(key, list(overlay_labels), n=3, cutoff=0.5)
            shown = ", ".join(repr(overlay_labels[k]) for k in nearest) or "none close"
            raise ScopingError(
                f"frameworks.scoping.{framework}.overlays: {declared!r} is not an overlay level in "
                f"the loaded {framework} catalog (nearest: {shown})."
            )
        wanted.add(key)

    counted: list[str] = []
    stood_in: dict[str, str] = {}
    scoped_out: dict[str, list[tuple[str, str]]] = {}

    def leave_out(kind: str, requirement_id: str, why: str) -> None:
        scoped_out.setdefault(kind, []).append((requirement_id, why))

    for control in controls:
        maturity = [r for r in control.requirements if r.level_kind == MATURITY]
        if scope.maturity is None:
            # No maturity declared: every maturity statement counts, as with
            # no scope at all, numbered or not. Only overlays are scoped.
            counted.extend(r.requirement_id for r in maturity)
        else:
            numbered: list[tuple[int, Requirement]] = []
            for r in maturity:
                n = _maturity_number(r.level)
                if n is None:
                    leave_out(
                        UNNUMBERED,
                        r.requirement_id,
                        f"{r.level!r} cannot be placed against Level {scope.maturity}",
                    )
                else:
                    numbered.append((n, r))
            eligible = [(n, r) for n, r in numbered if n <= scope.maturity]
            chosen = None
            if eligible:
                level, chosen = max(eligible, key=lambda pair: pair[0])
                counted.append(chosen.requirement_id)
                if level < scope.maturity:
                    stood_in[chosen.requirement_id] = (
                        f"{chosen.level}: no Level {scope.maturity} statement for "
                        f"{control.control_id}"
                    )
            for n, r in numbered:
                if r is chosen:
                    continue
                if chosen is None:
                    leave_out(
                        NO_STATEMENT_AT_OR_BELOW,
                        r.requirement_id,
                        f"{r.level}: {control.control_id} has no statement at or below "
                        f"Level {scope.maturity}",
                    )
                elif n > scope.maturity:
                    leave_out(ABOVE_DECLARED, r.requirement_id, f"{r.level}")
                else:
                    leave_out(
                        SUPERSEDED,
                        r.requirement_id,
                        f"{r.level}: {chosen.level} is counted for {control.control_id}",
                    )
        for r in control.requirements:
            if r.level_kind == MATURITY:
                continue
            if _label_key(r.level) in wanted:
                counted.append(r.requirement_id)
            else:
                leave_out(OVERLAY_NOT_DECLARED, r.requirement_id, r.level)
    return counted, stood_in, scoped_out


def scoped_out_levels(
    controls: list[Control], scopes: dict[str, LevelScope]
) -> dict[tuple[str, str], str]:
    """`{(framework key, requirement id): why}` for every level requirement
    the declared `scopes` leave out, by the rule `coverage` counts with.

    For synthesis (#416): a level statement coverage does not count is not
    sent to the model either, so the two never disagree about which levels
    apply.

    A scope key naming no loaded per-level framework is NOT refused here
    (9b on PR 419): `synthesize` loads the catalogs a run lists, a subset by
    design, so a key for a catalog left out of this run is not a typo.
    `coverage` counts every loaded catalog and still refuses one."""
    by_framework: dict[str, list[Control]] = {}
    for control in controls:
        if control.requirements:
            by_framework.setdefault(normalize_framework(control.framework), []).append(control)
    out: dict[tuple[str, str], str] = {}
    for framework, scoped in by_framework.items():
        if framework not in scopes:
            continue
        _, _, scoped_out = _scoped_requirements(scoped, framework, scopes[framework])
        for kind, pairs in scoped_out.items():
            for requirement_id, why in pairs:
                out[(framework, requirement_id)] = f"{kind}: {why}"
    return out


def _describe(scope: LevelScope) -> str:
    maturity = (
        f"maturity Level {scope.maturity} (the highest statement at or below it, per reference)"
        if scope.maturity is not None
        else "every maturity level (none declared)"
    )
    overlays = f"overlays {', '.join(scope.overlays)}" if scope.overlays else "no overlays"
    return f"{maturity}; {overlays}"


@dataclass
class SetAside:
    """Paragraphs a court vacated, left out of the denominator and named."""

    #: The judgment's short name, `Purl v. HHS`.
    judgment: str
    ids: list[str] = field(default_factory=list)


@dataclass
class CoverageReport:
    scope: str
    in_scope: list[str] = field(default_factory=list)
    #: requirement id -> the single topic that owns it
    covered: dict[str, str] = field(default_factory=dict)
    #: in-scope ids no topic claims
    orphaned: list[str] = field(default_factory=list)
    #: requirement id -> the competing topic names
    contested: dict[str, list[str]] = field(default_factory=dict)
    #: topic name -> anchors that match no control in the catalog (typos)
    unknown_anchors: dict[str, list[str]] = field(default_factory=dict)
    #: topic name -> anchors that are real controls but outside the current
    #: scope. Not an error: the PM and PT families sit in no baseline at all,
    #: and a topic legitimately anchors High-only controls while you analyze
    #: Moderate.
    out_of_scope_anchors: dict[str, list[str]] = field(default_factory=dict)
    #: owner -> number of in-scope requirements they answer for
    by_owner: dict[str, int] = field(default_factory=dict)
    #: topic name -> number of in-scope requirements it claims
    by_topic: dict[str, int] = field(default_factory=dict)
    framework_coverage: list[FrameworkCoverage] = field(default_factory=list)
    #: framework name -> the paragraphs a court vacated that are NOT in
    #: `in_scope` (#420, 80's ruling): text that binds nobody can't be work
    #: left undone. Read from the same `frameworks.vacated` manifests
    #: generation reads, so the two can't disagree about what is dropped.
    vacated: dict[str, SetAside] = field(default_factory=dict)
    #: topic name -> anchors that are text a court vacated. Named as that, not
    #: as "out of scope", which would give the reason no scope excludes them
    #: (1d on #420).
    vacated_anchors: dict[str, list[str]] = field(default_factory=dict)
    #: Policy name -> the topics that name it, where no `policies:` entry
    #: declares it (22, part 2; 80's 5919). Empty when no declared set was
    #: given, which is "not asked", not "all declared".
    undeclared_policies: dict[str, list[str]] = field(default_factory=dict)

    @property
    def is_clean(self) -> bool:
        """True when every in-scope control has exactly one owner and every
        anchor resolves."""
        return not (self.orphaned or self.contested or self.unknown_anchors)


def adopted_frameworks(topics, controls) -> set[str]:
    """Framework keys this registry actually anchors identifiers from.

    **"The catalogs a registry anchors" and "the catalogs a registry
    *could* anchor" read the same and are not.** `anchors_a_topic` answers
    the second -- membership in `TOPIC_ANCHORS` -- and using it as the
    coverage scope was correct only while every bundled catalog was either
    always-anchored (800-53) or never-anchorable (HIPAA, the CFR pair).

    The AI RMF is the first that is **anchorable but optional**, and there
    the two diverge with a bill attached: a customer who does no AI
    upgrades, gains 91 requirements in their denominator, and loses six
    points for work they never took on. Measured, not predicted -- 80
    stripped the AI topics out of the example registry and got 1105 in
    scope, 291 orphaned, against 1014 and 200 before.

    **Our own example registry is what hid it**, because it anchors the AI
    RMF and therefore looks fine.

    Adoption is per *catalog*, not per identifier: anchoring one AI RMF id
    brings all 91 into scope. Anything finer would make coverage
    unfalsifiable, since a registry would only ever be measured against
    what it had already claimed.
    """
    from policyforge.mapping.crosswalk import anchors_a_topic

    anchored = {a for topic in topics for a in topic.nist_controls}
    adopted = set()
    for control in controls:
        if not anchors_a_topic(control.framework):
            continue
        identifiers = {control.control_id} | {e.enhancement_id for e in control.enhancements}
        if identifiers & anchored:
            adopted.add(normalize_framework(control.framework))
    return adopted


def split_by_adoption(topics, controls):
    """`(in scope, bundled but unadopted, reachable via the crosswalk)`.

    The middle one exists so an unadopted catalog is **named rather than
    silently absent** -- 80's condition on the ruling, and the same move
    `_zero_row_reasons` already makes for a zero row. Excluding a catalog
    from the denominator is right; making it invisible is not, or the
    exclusion becomes a place for a framework to hide.
    """
    from policyforge.mapping.crosswalk import anchors_a_topic

    adopted = adopted_frameworks(topics, controls)

    # **Adoption only ever NARROWS.** If a registry adopts nothing, the
    # scope is everything anchorable, not nothing.
    #
    # Found by the suite rather than by thinking: a test registry anchoring
    # `ZZ-9` against a catalog of `AC-2` adopts no catalog, because its one
    # anchor is a typo. Without this line the report became "no topic
    # anchors any catalog on disk" -- which silently converts *you have a
    # typo* into *you have no registry*, and `unknown_anchors` exists
    # precisely to say the first.
    #
    # The empty-registry case lands the same way and should: somebody with
    # no topics yet wants to see everything as orphaned, not 0 of 0.
    #
    # This cannot reintroduce the defect the narrowing exists for, because
    # that case -- a real 800-53 registry meeting a newly bundled AI RMF --
    # adopts 800-53 and is never empty.
    if not adopted:
        adopted = {
            normalize_framework(c.framework) for c in controls if anchors_a_topic(c.framework)
        }

    in_scope, unadopted, reachable = [], [], []
    for control in controls:
        if not anchors_a_topic(control.framework):
            reachable.append(control)
        elif normalize_framework(control.framework) in adopted:
            in_scope.append(control)
        else:
            unadopted.append(control)
    return in_scope, unadopted, reachable


def unadopted_note(unadopted) -> list[str]:
    """Report lines naming catalogs that are bundled, anchorable, and
    anchored by nothing."""
    if not unadopted:
        return []
    by_framework: dict[str, int] = {}
    for control in unadopted:
        count = 1 + len(control.enhancements)
        by_framework[control.framework] = by_framework.get(control.framework, 0) + count
    lines = [
        "",
        "Bundled, anchorable, and not adopted",
        "-" * 60,
        "  These are excluded from the numbers above, because a catalog no",
        "  topic anchors is not a gap in your programme -- it is a framework",
        "  you have not taken on. Anchoring any one of its identifiers brings",
        "  the whole catalog into scope.",
    ]
    for name, count in sorted(by_framework.items()):
        lines.append(f"  {name}: {count} requirements, anchored by no topic")
    return lines


def scope_label(controls, baseline: str | None = None) -> str:
    """What the coverage denominator actually contained, named.

    **A percentage whose denominator can change without the reader being
    told is not a measurement.** When the AI RMF became anchorable the
    programme headline fell 80.3% -> 73.7% with the numerator unchanged
    at 814: ninety-one requirements arrived that a topic *could* own and
    none yet does. That is real information and it is indistinguishable,
    from the number alone, from work having been lost.

    The same arithmetic with the opposite meaning caused a live defect
    once already -- every installed catalog was being counted as
    anchorable, so requirements no topic could ever anchor were orphans
    by construction and installing a catalog lowered the score. That was
    wrong and was fixed. This is right and still needs saying, because a
    reader cannot tell the two apart by looking at the percentage.

    So the scope line names the catalogs, and a changed denominator is
    visible in the report rather than inferred from a number moving.
    """
    names = sorted({c.framework for c in controls})
    listed = ", ".join(names) if names else "no anchorable catalog"
    return f"{baseline} baseline ({listed})" if baseline else f"all controls ({listed})"


def _in_scope_pairs(controls: list[Control]) -> list[tuple[str, str]]:
    """(id, framework) for every control and enhancement: the parent rule
    reads the framework (#377), so the id alone is not enough."""
    pairs: list[tuple[str, str]] = []
    for control in controls:
        pairs.append((control.control_id, control.framework))
        pairs.extend((e.enhancement_id, control.framework) for e in control.enhancements)
    return pairs


def _in_scope_ids(controls: list[Control]) -> list[str]:
    ids: list[str] = []
    for control in controls:
        ids.append(control.control_id)
        ids.extend(e.enhancement_id for e in control.enhancements)
    return ids


def analyze_coverage(
    topics: list[Topic],
    nist_controls: list[Control],
    *,
    catalog: list[Control] | None = None,
    scope: str = "all controls",
    other_controls: list[Control] | None = None,
    crosswalk: dict[str, dict[str, list[str]]] | None = None,
    relationships: dict[tuple[str, str, str], str] | None = None,
    crosswalk_sources: dict[str, str] | None = None,
    scopes: dict[str, LevelScope] | None = None,
    vacated: dict | None = None,
    declared_policies: set[str] | None = None,
) -> CoverageReport:
    """Compute ownership coverage of `nist_controls` by `topics`.

    `nist_controls` should already be narrowed to whatever scope you care
    about — a baseline, say — since "orphaned" is only meaningful relative to
    a defined scope. Pass the full, unfiltered catalog as `catalog` so an
    anchor that's merely out of scope can be told apart from one that's a
    typo; without it, every anchor outside the scope looks like a bad ID.

    `relationships` maps (framework, requirement id, NIST id) to the
    relationship an organization recorded for that pair — see
    `crosswalk.overlay.relationships_for`.

    `crosswalk_sources` names who published each framework's mapping, by
    framework key (`registry.declared_crosswalk_sources`, #408). It is read
    from the manifests on disk only when a `source-untyped` framework is in
    the report, so no other report pays for it.

    `vacated` is `frameworks.vacated.declared_vacated`: paragraphs a court
    vacated (`VACATED`, text the rule added) are not requirements, so they
    leave the denominator and are named in `report.vacated` (80's ruling on
    #420, as on #409 for generation). A `vacated-revision` paragraph stays:
    the pre-rule wording binds again. **The caller passes it** -- reading
    the manifests costs about 0.15 s, which no report that has none should
    pay -- and `None` sets nothing aside. Both callers in `src` pass it, and
    `tests/test_hipaa_anchoring.py` fails a call that does not.
    """
    report = CoverageReport(scope=scope)
    if declared_policies is not None:
        known = {name.strip().casefold() for name in declared_policies}
        for topic in topics:
            if topic.policy and topic.policy.strip().casefold() not in known:
                report.undeclared_policies.setdefault(topic.policy, []).append(topic.name)
    report.in_scope = []
    for requirement_id, framework in _in_scope_pairs(nist_controls):
        status = (vacated or {}).get(normalize_framework(framework))
        if status is not None and status.status(requirement_id) == VACATED:
            aside = report.vacated.setdefault(
                framework, SetAside(status.judgment.split(",")[0].strip())
            )
            aside.ids.append(requirement_id)
        else:
            report.in_scope.append(requirement_id)
    in_scope = set(report.in_scope)
    set_aside = {i for aside in report.vacated.values() for i in aside.ids}
    # With no catalog given, the ids of `nist_controls` themselves, vacated
    # ones included: an anchor a court vacated is named as that, not as an
    # id the catalog doesn't have (a caller that passes no `catalog`).
    catalog_ids = set(_in_scope_ids(catalog if catalog is not None else nist_controls))

    direct: dict[str, list[str]] = {}
    inherited: dict[str, list[str]] = {}
    children: dict[str, list[str]] = {}
    for requirement_id, framework in _in_scope_pairs(nist_controls):
        parent = parent_of(requirement_id, framework)
        if parent:
            children.setdefault(parent, []).append(requirement_id)

    for topic in topics:
        for anchor in topic.nist_controls:
            if anchor not in catalog_ids:
                report.unknown_anchors.setdefault(topic.name, []).append(anchor)
                continue
            if anchor not in in_scope:
                if anchor in set_aside:
                    report.vacated_anchors.setdefault(topic.name, []).append(anchor)
                else:
                    report.out_of_scope_anchors.setdefault(topic.name, []).append(anchor)
                continue
            direct.setdefault(anchor, []).append(topic.name)
            # Anchoring a control also claims its enhancements.
            for child in children.get(anchor, []):
                inherited.setdefault(child, []).append(topic.name)

    owner_of = {t.name: t.owner for t in topics}
    for requirement_id in report.in_scope:
        # A direct claim beats an inherited one, so a specifically-anchored
        # enhancement can sit with a different team than its parent without
        # either being reported as contested.
        claimants = direct.get(requirement_id) or inherited.get(requirement_id) or []
        unique = sorted(set(claimants))
        if not unique:
            report.orphaned.append(requirement_id)
        elif len(unique) == 1:
            report.covered[requirement_id] = unique[0]
            report.by_topic[unique[0]] = report.by_topic.get(unique[0], 0) + 1
            owner = owner_of[unique[0]]
            report.by_owner[owner] = report.by_owner.get(owner, 0) + 1
        else:
            report.contested[requirement_id] = unique

    for topic in topics:
        report.by_topic.setdefault(topic.name, 0)
        report.by_owner.setdefault(topic.owner, 0)

    if other_controls and crosswalk:
        report.framework_coverage = _framework_coverage(
            other_controls,
            crosswalk,
            owned=set(report.covered) | set(report.contested),
            relationships=relationships or {},
            crosswalk_sources=crosswalk_sources,
            scopes=scopes,
        )

    return report


def _framework_coverage(
    other_controls: list[Control],
    crosswalk: dict[str, dict[str, list[str]]],
    *,
    owned: set[str],
    relationships: dict[tuple[str, str, str], str],
    crosswalk_sources: dict[str, str] | None = None,
    family_links: dict[str, dict[str, frozenset[str]]] | None = None,
    scopes: dict[str, LevelScope] | None = None,
) -> list[FrameworkCoverage]:
    """Which non-NIST requirements are reachable from an owned NIST control.

    `family_links` (`declared_family_links`, #448) are named on their
    framework and counted in nothing; they default to the manifests."""
    if family_links is None:
        from policyforge.frameworks.registry import config_or_defaults, declared_family_links

        family_links = declared_family_links(config_or_defaults("family links were read"))
    reachable: dict[str, set[str]] = {}
    in_part: dict[str, set[str]] = {}
    # Read from the same table as `reaches_in_full`, so both callers agree.
    untyped = {f for (f, _, _), rel in relationships.items() if rel == SOURCE_UNTYPED}
    if untyped and crosswalk_sources is None:
        from policyforge.frameworks.registry import (
            config_or_defaults,
            declared_crosswalk_sources,
        )

        crosswalk_sources = declared_crosswalk_sources(
            config_or_defaults("crosswalk sources were read")
        )
    linked: dict[str, set[str]] = {}
    for mapped in crosswalk.values():
        for framework, equivalent_ids in mapped.items():
            linked.setdefault(framework, set()).update(equivalent_ids)
    for nist_id in owned:
        for framework, equivalent_ids in crosswalk.get(nist_id, {}).items():
            for requirement_id in equivalent_ids:
                if reaches_in_full(framework, requirement_id, nist_id, relationships):
                    reachable.setdefault(framework, set()).add(requirement_id)
                else:
                    in_part.setdefault(framework, set()).add(requirement_id)

    # **Counted in the unit the crosswalk records.** A control carrying level
    # requirements (HITRUST) is counted per requirement, every level the
    # catalog carries, because `build_crosswalk` records its mappings under
    # "01.a Level 1", never under "01.a". Listing the control id instead made
    # the two id spaces disjoint and HITRUST read 0 whatever the topics owned
    # (#282). Not rolled up to the control: that would claim a mapping for
    # levels that never had one -- `_requirement_sources`' own reasoning, and
    # the ruling that the project does not assert what its sources withheld
    # (80, on #282). Which levels APPLY to an organisation is scoping (#283).
    if scopes is None:
        from policyforge.frameworks.registry import config_or_defaults

        scopes = level_scopes(config_or_defaults("level scoping was read"))
    by_framework: dict[str, list[str]] = {}
    per_level: set[str] = set()
    # A declared scope (#283) picks which level requirements count; with
    # none, every level is counted, as #282 ruled, and the report says so.
    scoped: dict[str, list[Control]] = {}
    for control in other_controls:
        framework = normalize_framework(control.framework)
        ids = by_framework.setdefault(framework, [])
        if control.requirements:
            per_level.add(framework)
            if framework in scopes:
                scoped.setdefault(framework, []).append(control)
            else:
                ids.extend(r.requirement_id for r in control.requirements)
        else:
            ids.append(control.control_id)
            ids.extend(e.enhancement_id for e in control.enhancements)
    # A declared key that names no loaded per-level framework is refused (1d
    # on #461): `hitrust` normalises to `hitrust`, but the importer writes
    # `HITRUST-CSF`, so the scope was skipped in silence and the report then
    # told the user who declared it that nothing was declared. With no
    # per-level framework loaded at all there is no level line to mislead,
    # so a declaration is simply unused.
    unmatched = sorted(set(scopes) - per_level)
    if unmatched and per_level:
        raise ScopingError(
            f"frameworks.scoping names {', '.join(unmatched)}, which is not a loaded "
            f"per-level framework; the loaded ones are {', '.join(sorted(per_level))}. "
            "Use that key."
        )
    # The level rules are public (`levelled`, #32 part 2), so this needs no
    # plugin: the field is measured for every HITRUST catalog, whether or not
    # the parser that wrote it is installed. It was an optional import of the
    # HITRUST parser, which would have gone silently empty once the parser
    # moved (1d on #34).
    from policyforge.ingest import levelled

    at_cut_width: dict[str, Counter] = {}
    for control in other_controls:
        if normalize_framework(control.framework) == normalize_framework(levelled.FRAMEWORK):
            for r in control.requirements:
                if levelled.level_may_be_truncated(r.level):
                    at_cut_width.setdefault(normalize_framework(control.framework), Counter())[
                        r.level
                    ] += 1
    stood_in: dict[str, dict[str, str]] = {}
    scoped_out: dict[str, dict[str, list[tuple[str, str]]]] = {}
    for framework, controls in scoped.items():
        counted, stood_in[framework], scoped_out[framework] = _scoped_requirements(
            controls, framework, scopes[framework]
        )
        by_framework[framework].extend(counted)

    coverage: list[FrameworkCoverage] = []
    for framework, requirement_ids in sorted(by_framework.items()):
        hit = reachable.get(framework, set())
        part = in_part.get(framework, set()) - hit
        coverage.append(
            FrameworkCoverage(
                framework=framework,
                covered=sorted(r for r in requirement_ids if r in hit),
                partial=sorted(r for r in requirement_ids if r in part),
                uncovered=sorted(r for r in requirement_ids if r not in hit and r not in part),
                per_level=framework in per_level,
                unmapped_by_source=(
                    sorted(r for r in requirement_ids if r not in linked.get(framework, set()))
                    if framework in untyped
                    else None
                ),
                source_name=(crosswalk_sources or {}).get(framework, "")
                if framework in untyped
                else "",
                family_links={
                    requirement_id: sorted(families)
                    for requirement_id, families in sorted(family_links.get(framework, {}).items())
                },
                scoping=_describe(scopes[framework]) if framework in scoped else "",
                stood_in=stood_in.get(framework, {}),
                scoped_out={
                    kind: sorted(scoped_out[framework][kind])
                    for kind in SCOPED_OUT_KINDS
                    if kind in scoped_out.get(framework, {})
                },
                levels_at_cut_width=dict(sorted(at_cut_width.get(framework, {}).items())),
            )
        )
    return coverage


def format_report(report: CoverageReport, *, show_all: bool = False) -> str:
    """Render the report for a terminal."""
    lines: list[str] = []
    total = len(report.in_scope)
    owned = len(report.covered)
    lines.append(f"Coverage — scope: {report.scope}")
    lines.append("=" * 60)
    lines.append(f"  In scope        {total}")
    lines.append(f"  Owned           {owned}" + (f" ({owned / total:.0%})" if total else ""))
    lines.append(f"  Orphaned        {len(report.orphaned)}")
    lines.append(f"  Contested       {len(report.contested)}")
    for framework, aside in sorted(report.vacated.items()):
        lines.append(
            f"  {len(aside.ids)} vacated paragraph(s) of {framework} not counted ({aside.judgment})"
        )

    if report.unknown_anchors:
        lines.append("")
        lines.append("Unknown anchors — these control IDs don't exist in the catalog")
        lines.append("-" * 60)
        for topic, anchors in sorted(report.unknown_anchors.items()):
            lines.append(f"  {topic}: {', '.join(anchors)}")

    if report.undeclared_policies:
        lines.append("")
        lines.append("Policies a topic names but `policies:` doesn't declare")
        lines.append("-" * 60)
        for policy, topics in sorted(report.undeclared_policies.items()):
            lines.append(f"  {policy}: {', '.join(topics)}")

    if report.vacated_anchors:
        lines.append("")
        lines.append("Anchored but vacated — a court vacated this text; it binds nobody")
        lines.append("-" * 60)
        for topic, anchors in sorted(report.vacated_anchors.items()):
            lines.append(f"  {topic}: {', '.join(anchors)}")

    if report.out_of_scope_anchors:
        total_out = sum(len(a) for a in report.out_of_scope_anchors.values())
        lines.append("")
        lines.append(
            f"Anchored but out of scope ({total_out}) — real controls this scope doesn't include"
        )
        lines.append("-" * 60)
        for topic, anchors in sorted(report.out_of_scope_anchors.items()):
            shown = anchors if show_all else anchors[:10]
            suffix = "" if len(shown) == len(anchors) else f", +{len(anchors) - len(shown)} more"
            lines.append(f"  {topic}: {', '.join(shown)}{suffix}")

    if report.contested:
        lines.append("")
        lines.append("Contested — more than one topic claims these")
        lines.append("-" * 60)
        for requirement_id, topics in sorted(report.contested.items()):
            lines.append(f"  {requirement_id:<12} {' | '.join(topics)}")

    if report.orphaned:
        lines.append("")
        lines.append("Orphaned — no topic claims these")
        lines.append("-" * 60)
        shown = report.orphaned if show_all else report.orphaned[:40]
        for index in range(0, len(shown), 8):
            lines.append("  " + ", ".join(shown[index : index + 8]))
        if len(shown) < len(report.orphaned):
            lines.append(f"  ... and {len(report.orphaned) - len(shown)} more (--show-all)")

    if report.by_owner:
        lines.append("")
        lines.append("Requirements owned, by team")
        lines.append("-" * 60)
        for owner, count in sorted(report.by_owner.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"  {count:>4}  {owner}")

    for framework in report.framework_coverage:
        lines.append("")
        lines.append(f"{framework.framework.upper()} reachable via the crosswalk")
        lines.append("-" * 60)
        lines.append(
            f"  {len(framework.covered)} of {framework.total} requirements map to an "
            "owned NIST control"
        )
        if framework.per_level and framework.scoping:
            # 80's ruling on #283: say what was counted, and which lower
            # level stood in where a reference had none at the declared one.
            lines.append(
                f"  counted per level requirement at {framework.scoping} "
                "(frameworks.scoping in config)"
            )
            if framework.stood_in:
                lines.append(
                    f"  {len(framework.stood_in)} reference(s) have no statement at the "
                    "declared maturity level, so a lower one is counted:"
                )
                shown = sorted(framework.stood_in.items())
                shown = shown if show_all else shown[:12]
                for requirement_id, why in shown:
                    lines.append(f"    {requirement_id}  ({why})")
                if len(shown) < len(framework.stood_in):
                    lines.append(
                        f"    ... and {len(framework.stood_in) - len(shown)} more (--show-all)"
                    )
            # 80's ruling on #5: anything the scope leaves out is named, so a
            # reader can tell "scoped out" from "never there".
            left_out = sum(len(ids) for ids in framework.scoped_out.values())
            if left_out:
                lines.append(
                    f"  {left_out} requirement(s) are scoped out and not counted; with the "
                    f"{framework.total} counted, that is all {framework.total + left_out} "
                    "in the catalog:"
                )
                for kind, pairs in framework.scoped_out.items():
                    lines.append(f"    {len(pairs)} {kind}:")
                    shown = pairs if show_all else pairs[:12]
                    for requirement_id, why in shown:
                        lines.append(f"      {requirement_id}  ({why})")
                    if len(shown) < len(pairs):
                        lines.append(f"      ... and {len(pairs) - len(shown)} more (--show-all)")
        elif framework.per_level:
            lines.append(
                "  counted per level requirement (all levels in the catalog), the unit "
                "HITRUST publishes its mappings in"
            )
            lines.append(
                "  No level scoping is declared, so every level counts, including levels "
                "that may not apply to you. Declare yours under frameworks.scoping in config."
            )
        if framework.partial:
            lines.append(
                f"  {len(framework.partial)} more are reached only in part — no owned control "
                "is recorded as covering all of the requirement:"
            )
            shown = framework.partial if show_all else framework.partial[:12]
            for index in range(0, len(shown), 4):
                lines.append("    " + ", ".join(shown[index : index + 4]))
            if len(shown) < len(framework.partial):
                lines.append(f"    ... and {len(framework.partial) - len(shown)} more (--show-all)")
        if framework.unmapped_by_source is not None:
            # Generic wording, the source named from the manifest (80, on #449).
            source = (
                f"{framework.source_name} (the source of this crosswalk)"
                if framework.source_name
                else "the source of this crosswalk"
            )
            lines.append(
                f"  {source[0].upper()}{source[1:]} publishes this mapping untyped and "
                "declares it incomplete, so every link counts as partial."
            )
            if framework.unmapped_by_source:
                lines.append(
                    f"  {len(framework.unmapped_by_source)} of the requirements not reached "
                    f"are not mapped by {source}, which is not the same as having no "
                    "800-53 equivalent."
                )
        if framework.family_links:
            # 80's ruling on #448: named, with the source, and never counted.
            pairs = "; ".join(
                f"{rid} -> {', '.join(families)}"
                for rid, families in framework.family_links.items()
            )
            n = sum(len(families) for families in framework.family_links.values())
            by = f" in {framework.source_name}" if framework.source_name else ""
            lines.append(
                f"  {n} family-level link(s){by} ({pairs}) are shown in `satisfies` and "
                "reach documents in `drift`, but never count as coverage."
            )
        if framework.uncovered:
            shown = framework.uncovered if show_all else framework.uncovered[:12]
            lines.append("  Not reached:")
            for index in range(0, len(shown), 4):
                lines.append("    " + ", ".join(shown[index : index + 4]))
            if len(shown) < len(framework.uncovered):
                lines.append(
                    f"    ... and {len(framework.uncovered) - len(shown)} more (--show-all)"
                )

    return "\n".join(lines)
