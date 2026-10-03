"""Cross-framework control crosswalk.

Builds a NIST-800-53-anchored table of which controls in other frameworks
correspond to each NIST control, the way the source vault's synthesis docs
do it: `{nist_control_id: {framework: [equivalent_ids]}}`.

Two sources of crosswalk data are folded together:

1. A NIST control's own `source_crosswalk` (populated by
   `ingest/nist_vault_loader.py` from its "Cross-Framework Mappings" table).
2. A non-NIST control's `source_crosswalk`, if it happens to point back at a
   NIST control ID (e.g. a FedRAMP control loaded independently that
   declares its own NIST equivalent).

Both are read at the enhancement level as well as the control level: a
framework may publish its crosswalk against sub-requirements rather than
(or as well as) top-level controls. NIST's own HIPAA-to-800-53 crosswalk
does exactly this, mapping each Required/Addressable implementation
specification separately from its parent Standard, so an enhancement's
mapping is recorded under the enhancement's own ID rather than folded into
its parent's.

Crosswalk cell values are free text (e.g. "AC-2 (same ID)", "AC-2, AC-3"),
so IDs are extracted with a regex rather than assumed to be a single clean
token.
"""

from __future__ import annotations

import re

from policyforge.ingest.schema import Control

_ID_RE = re.compile(r"[A-Za-z]{1,4}-\d+(?:\([A-Za-z0-9]+\))?")


def normalize_framework(name: str) -> str:
    """Normalize a framework name: "NIST 800-53" -> "nist-800-53".

    Public because other pipeline stages (e.g. synthesis/merge.py) need to
    look controls up by the same (framework, control_id) key this module
    uses internally.

    This used to be the first word, and that was the defect. "NIST" alone
    spans 800-53, 800-171, 800-172, 800-137 and the Cybersecurity
    Framework, so every NIST-family catalog filed under one key and their
    requirement identifiers merged into one set. An 800-171 identifier
    cited as 800-53 was then *found* in that shared bucket and counted as
    evidence rather than reported as unknown — a citation resolving to the
    wrong catalog, silently, which for a tool an assessor reads is the one
    failure mode this project exists to prevent.

    The reasoning had already been done and written down, one function
    below, for the HITRUST path: *folding those together would file CSF
    outcome ids as 800-53 controls*. It simply never reached the catalog
    path. So both now share `FRAMEWORK_ALIASES`, and there is one
    definition of what a framework name keys to instead of two that
    disagree.

    The first word remains the fallback, which is what keeps every catalog
    not named in the table — including one a user brings — working exactly
    as before.

    **A catalog's declared key wins (#295).** A name that a catalog's
    `framework.yaml` declares a key for (`framework_id:`) keys to that key, whatever the
    prose would give, so a sibling of a pinned name cannot fall through to
    the first word. The declarations are read from the catalog directories
    once, lazily (`frameworks.registry.declared_keys`), so the answer does
    not depend on which catalog was loaded first.
    """
    declared = _declared_keys().get(" ".join(name.lower().split()))
    return declared or prose_framework_key(name)


_DECLARED_KEYS: dict[str, str] | None = None


def _declared_keys() -> dict[str, str]:
    """Every catalog's declared name -> key, read once per process (#295)."""
    global _DECLARED_KEYS
    if _DECLARED_KEYS is None:
        # Assigned only once the build has succeeded (1d on #344): a failed
        # build that left `{}` behind would key every later lookup by prose,
        # silently dropping every declaration. Building cannot recurse into
        # this function -- `declared_keys` keys names with
        # `prose_framework_key` -- so nothing needs a placeholder.
        _DECLARED_KEYS = declared_keys_from_config()
    return _DECLARED_KEYS


def declared_keys_from_config() -> dict[str, str]:
    """`declared_keys` for this project's configured search paths.

    **Keying never fails on the config file** (1d on #344): an unreadable
    config is named once in a `FrameworkKeyWarning` and the defaults are
    read, through `registry.config_or_defaults`, the one copy of that rule.
    """
    from policyforge.frameworks.registry import (
        FrameworkKeyWarning,
        config_or_defaults,
        declared_keys,
    )

    config = config_or_defaults("framework keys were declared", FrameworkKeyWarning)
    return declared_keys(config)


def reset_declared_keys() -> None:
    """Forget the cached declarations, so the next lookup reads the
    directories again: after an importer writes a new catalog, or in tests."""
    global _DECLARED_KEYS
    _DECLARED_KEYS = None


def prose_framework_key(name: str) -> str:
    """The key a framework NAME gives on its own: the first alias needle it
    contains, else its first word. What `normalize_framework` falls back to
    where no catalog declares the name (#295)."""
    lowered = name.strip().lower()
    for needle, framework in FRAMEWORK_ALIASES:
        if _needle_found(needle, lowered):
            return framework
    return lowered.split()[0] if lowered.split() else ""


def _needle_found(needle: str, lowered: str) -> bool:
    """Whether `needle` occurs in `lowered` -- and, for a needle ending in a
    digit, occurs with NO digit after it.

    A document number is a prefix of its successors: as a plain substring,
    `ai 100-1` matched NIST AI 100-10 through 100-19 and filed them under the
    RMF, and `ai 100-2` took 100-20 onward (80 and 1d, on #296). `(?!\\d)`,
    not a word boundary, because the Taxonomy's 2025 edition is written
    `100-2e2025` -- a letter follows, and it must stay mapped. The rule holds
    for every digit-ending needle, not only the two that exposed it:
    `800-53` must not take a hypothetical 800-530 either.
    """
    if not needle[-1].isdigit():
        return needle in lowered
    return re.search(re.escape(needle) + r"(?!\d)", lowered) is not None


def _is_nist(framework: str) -> bool:
    return normalize_framework(framework) == NIST_ANCHOR


def _extract_ids(raw: str) -> list[str]:
    """Pull control/requirement IDs out of a free-text crosswalk cell,
    e.g. "AC-2 (same ID)" -> ["AC-2"], "AC-2, AC-3" -> ["AC-2", "AC-3"]."""
    return _ID_RE.findall(raw)


def _crosswalk_sources(control: Control) -> list[tuple[str, dict[str, str]]]:
    """Every (requirement_id, source_crosswalk) pair a Control carries — the
    control itself, then each of its enhancements. Enhancements are yielded
    under their own IDs so a sub-requirement's mapping stays attributable to
    that sub-requirement."""
    sources = [(control.control_id, control.source_crosswalk)]
    sources.extend((e.enhancement_id, e.source_crosswalk) for e in control.enhancements)
    return [(id_, cw) for id_, cw in sources if cw]


#: Which framework a declared name keys to, where the first word will not do.
#:
#: Framework names are written in full by the people and exports that supply
#: them — HITRUST names its authoritative sources as "NIST SP 800-53 r5", a
#: catalog declares "HIPAA Security Rule", a user brings "NIST Cybersecurity
#: Framework" — and the first word of several of those is the same word.
#: "NIST" alone spans 800-53, 800-171, 800-172, 800-137 and the Cybersecurity
#: Framework, whose identifiers look nothing alike: `AC-2`, `03.01.01`,
#: `GV.PO-02`. Folding those together files CSF outcome ids as 800-53
#: controls, so the distinguishing substring is matched before the first-word
#: fallback.
#:
#: **Order is significant — first match wins.** The NIST document numbers
#: come first because they are the most specific thing a name can carry.
#:
#: The abbreviations are here because people write them. "NIST CSF" and
#: "NIST CSF 2.0" — the current version's actual name — miss a needle that
#: only spells the framework out, and missing it means falling through to the
#: first word, which is the bug this table exists to prevent.
#:
#: **Every needle here must be as narrow as the thing it names.** The CSF
#: entries say `nist csf` rather than a bare `csf` because "HITRUST CSF"
#: contains "csf" and belongs to HITRUST. A first draft of this table also
#: carried a `hitrust` needle to guard that, which was unnecessary — the
#: narrow needles already miss "HITRUST CSF" — and actively wrong: it
#: swallowed the framework id `hitrust-csf` and returned `hitrust`, quietly
#: merging a catalog that had been keyed correctly for as long as it existed.
#: A guard against a hazard that is not there is not free.
FRAMEWORK_ALIASES: tuple[tuple[str, str], ...] = (
    ("800-53", "nist-800-53"),
    ("800-171", "nist-800-171"),
    # The two CFR catalogs whose declared names had to become citable: a
    # name beginning with a digit is not a tag, so they were renamed and
    # pinned here rather than keying to `information` and `substance`.
    ("information blocking", "cfr-171-information-blocking"),
    ("substance use disorder", "cfr-42-part-2-sud-records"),
    # The AI pair, pinned before either catalog exists — the cheap moment.
    # `NIST AI RMF` would take the bare `nist` key, which is the orphan the
    # NIST-family split existed to remove; `HITRUST AI Security
    # Certification` collides with `HITRUST CSF` outright, and it is an
    # add-on you cannot hold standalone, so any user with one has both.
    # Two needles for AI RMF because NIST writes the name both ways and
    # neither spelling contains the other.
    # The Playbook BEFORE the Core, because first match wins and "NIST AI RMF
    # Playbook" contains "ai rmf": without this it keyed to `nist-ai-rmf` and
    # merged NIST's voluntary suggested actions into the Core's outcomes --
    # two separately versioned publications in one bucket (#177).
    ("ai rmf playbook", "nist-ai-rmf-playbook"),
    ("ai rmf", "nist-ai-rmf"),
    ("ai risk management", "nist-ai-rmf"),
    # The RMF's document number, which NIST prints on its cover and CPRT
    # uses as its id (`AI_100_1`). Without it `NIST AI 100-1` fell to the
    # bare `nist` key while both prose spellings were pinned (#176).
    ("ai 100-1", "nist-ai-rmf"),
    # Its sibling, AI 100-2, the Adversarial ML Taxonomy, published beside the
    # RMF. Pinned by its DOCUMENT NUMBER only, which no other publisher uses.
    # **Not pinned, deliberately** (1d, on #296): "ai taxonomy" -- OECD, the
    # EU, ISO, Microsoft and MITRE ATLAS all publish an "AI Taxonomy", and a
    # needle would merge them into NIST's key -- nor "nist ai taxonomy", which
    # no NIST source has been found to declare (CPRT's `AITAXONOMY` is a code
    # identifier, not a name). Nor the title words "adversarial machine
    # learning", for the same reason as "ai taxonomy". A catalog named only
    # "AI Taxonomy" keeps the bare `ai` orphan; keying it is #295's job.
    ("ai 100-2", "nist-ai-100-2"),
    ("hitrust ai", "hitrust-ai"),
    # SOC 2's criteria (#410). Narrow on purpose: a bare "soc" or "aicpa" is
    # a first word other names share, as "csf" is HITRUST's as well as NIST's.
    ("trust services criteria", "aicpa-tsc"),
    ("800-172", "nist-800-172"),
    ("800-137", "nist-800-137"),
    ("cybersecurity framework", "nist-csf"),
    ("nist csf", "nist-csf"),
    ("arc-ampe", "arc-ampe"),
    ("hipaa security rule", "hipaa"),
    ("hipaa privacy rule", "hipaa-privacy"),
    ("hipaa breach", "hipaa-breach"),
    ("fedramp", "fedramp"),
    ("govramp", "govramp"),
    ("iso/iec 27001", "iso-27001"),
    ("iso/iec 27002", "iso-27002"),
    ("pci dss", "pci-dss"),
    # The ONC catalog's own declared name (#179): a name this project has
    # READ, because it declares it. Its siblings stay unpinned -- see below.
    ("onc certification criteria", "cfr-170-315-onc-certification"),
)
# **ONC's siblings are deliberately absent** (#176). The ONC catalog's own
# name is pinned above (#179); every OTHER ONC name keys to the bare `onc` --
# "ONC Health IT Certification Program", "ONC Certification Program
# Requirements" -- which is harmless while no second ONC catalog exists and
# becomes a collision the day one lands (Part 170's Subparts D and E are
# also "ONC ... Certification"). They
# are not pre-pinned because their declared names are a guess until someone
# reads the regulation: this table is for names that have been READ, and a
# pin written in anticipation reads as decided. `test_onc_siblings_share_
# the_bare_key` pins today's behaviour, so the next ONC catalog meets this
# decision instead of the gap. The structural remedy -- a key the catalog
# declares -- is #295.

#: The old name, kept because the table's first readers were the HITRUST
#: importer's authoritative-source names. It is the same table; the catalog
#: path simply never used it, which is what let two definitions of one
#: concept drift apart.
HITRUST_SOURCE_ALIASES = FRAMEWORK_ALIASES

#: Which framework is NIST 800-53 proper, and so may anchor a row of the
#: crosswalk. The crosswalk is 800-53-anchored by construction, and an
#: identifier from any other source is not an anchor no matter how much it
#: looks like one.
#:
#: This was `"nist"` until the NIST family got one key each. A bare `nist`
#: cannot be the anchor any more, and not only for tidiness: while 800-53
#: was filed under it, `resolve_framework` found `nist` in the index and
#: returned it before its own ambiguity rule could run, so a citation
#: reading `[NIST AC-2]` resolved to 800-53 by short-circuit rather than
#: because anything had established which NIST catalog was meant.
NIST_ANCHOR = "nist-800-53"

#: The frameworks a **topic registry** may anchor identifiers from.
#:
#: **This is a different concept from `NIST_ANCHOR` above, and they were one
#: constant until 2026-09-20.** Keeping them apart is the whole point of
#: this definition, so the distinction is worth stating precisely:
#:
#:     NIST_ANCHOR    the crosswalk anchor - what every other framework's
#:                    requirements are mapped ONTO. Singular by
#:                    construction: an overlay row asserts "this
#:                    requirement and that control are the same
#:                    obligation", which needs one side to be fixed.
#:
#:     TOPIC_ANCHORS  which catalogs a topic may claim ids from. A set,
#:                    because a programme can be organised around more
#:                    than one catalog without either becoming the thing
#:                    the other maps onto.
#:
#: **Why the split is safety-critical rather than tidiness.** The obvious
#: way to let topics anchor a second catalog is to grep `NIST_ANCHOR` and
#: widen it. That also widens the crosswalk-anchor sites, and a catalog
#: that becomes a crosswalk *target* becomes something `crosswalk seed`
#: will happily generate a mapping for. For the AI RMF that mapping is
#: refused on product grounds -- it would assert an 800-53 control
#: ACHIEVES an AI RMF outcome, the claim NIST declined to make when it
#: split the Playbook out. So the natural refactor silently publishes the
#: thing we decided not to publish, while looking like the change that was
#: asked for.
#:
#: Widening THIS set cannot do that. Every site is consciously assigned to
#: one concept or the other, and `test_anchor_concepts.py` holds the
#: assignment: it widens `TOPIC_ANCHORS` and requires that seeding a
#: crosswalk stays refused.
#:
#: The AI RMF joined on 2026-09-20, on the user's decision, so a topic can
#: anchor `Govern 1` and have its documents genuinely cite the framework.
#: **Adding a catalog here grows `/coverage`'s denominator**, because its
#: requirements become things a topic could own and therefore things a
#: topic can fail to own. That is a real report rather than a regression,
#: but it must be read as one -- the report names the catalogs in scope for
#: exactly that reason.
TOPIC_ANCHORS: frozenset[str] = frozenset({NIST_ANCHOR, "nist-ai-rmf"})

#: The value `ANCHOR_DECISIONS` gives a catalog that topics may anchor.
ANCHORS = "anchors"

#: **Every catalog's answer to "may a topic anchor it?", written down** (#175).
#: Keyed by catalog DIRECTORY -- the unit someone adds -- bundled and
#: bring-your-own alike. The value is `ANCHORS`, or the reason it is not.
#:
#: Adding a catalog forced four declarations (bundled or BYOC, licence, alias,
#: README row) and never this one, which is the one that decides whether its
#: requirements enter `/coverage`'s denominator and whether synthesis can
#: retrieve them. Wrong either way, it fails silently: omitted, and the catalog
#: is inert (#171); included, and every user's coverage figure moves for a
#: framework they never adopted (#169). `test_anchor_decisions.py` derives the
#: catalog population from `data/frameworks/` and the scaffold lists and
#: refuses a catalog with no entry here, an entry for no catalog, and any
#: disagreement with `TOPIC_ANCHORS`.
#:
#: The reasons record the state as of 2026-09-24, drawn from the rulings on
#: each catalog's issue; changing one is a product decision (80), not an edit.
ANCHOR_DECISIONS: dict[str, str] = {
    "nist-800-53-r5": ANCHORS,
    "nist-ai-rmf": ANCHORS,
    "arc-ampe": "reached through its crosswalk to 800-53, not anchored beside it",
    "fedramp": "reached through its crosswalk to 800-53, not anchored beside it",
    "hipaa-security-rule": "reached through its crosswalk to 800-53, not anchored beside it",
    "nist-800-171-r3": (
        "reached through NIST's own mapping to 800-53, carried since #259; a topic "
        "anchors the 800-53 controls it maps to"
    ),
    "cfr-171-information-blocking": (
        "conditions of an exception, not controls to implement: refused by design "
        "(NOT_CROSSWALK_ANCHORABLE)"
    ),
    "cfr-170-315-onc-certification": (
        "a certification criterion describes what a certified product can do, not what "
        "an organization implements, so a topic owning one would count a product "
        "capability as organizational coverage (its README; 80, on #308)"
    ),
    "cfr-42-part-2-sud-records": (
        "no published mapping was found (#264); reached only through a crosswalk an "
        "organisation seeds"
    ),
    "hipaa-privacy-rule": (
        "no published mapping to 800-53 (#409): the Privacy Rule governs uses and "
        "disclosures, not security controls; reached only through a crosswalk an "
        "organisation seeds (80, on #409)"
    ),
    "hipaa-breach-notification-rule": (
        "no published mapping to 800-53 (#409); reached only through a crosswalk an "
        "organisation seeds (80, on #409)"
    ),
    "nist-csf-2-0": (
        "outcomes, like the AI RMF Core's, reached through NIST's own mapping to 800-53 "
        "(OLIR 186), whose links read as partial; a topic anchors the 800-53 controls "
        "it maps to (80, on #408)"
    ),
    "nist-ai-rmf-playbook": (
        "never anchored or counted in /coverage, because its actions are voluntary; "
        "a Standard retrieves them through the Core subcategories its topic anchors, "
        "and no Policy or Procedure is given them (#301)"
    ),
    "govramp": (
        "bring-your-own: no ids ship, so there is nothing to anchor until a user "
        "supplies the catalog"
    ),
    "hitrust-ai": (
        "bring-your-own: no ids ship, so there is nothing to anchor until a user "
        "supplies the catalog"
    ),
    "soc2-tsc": (
        "bring-your-own: no ids ship, so there is nothing to anchor until a user "
        "supplies the catalog; its AICPA mapping, if supplied, is read as "
        "source-untyped (80, on #410)"
    ),
    "hitrust-csf": (
        "bring-your-own: no ids ship, so there is nothing to anchor until a user "
        "supplies the catalog"
    ),
}


def anchors_a_topic(framework: str) -> bool:
    """Whether a topic registry may anchor identifiers from `framework`.

    The B-side predicate. Use this wherever the question is *can a topic
    claim these ids*; use `NIST_ANCHOR` directly where the question is
    *what does the crosswalk map onto*.
    """
    return normalize_framework(framework) in TOPIC_ANCHORS


def hitrust_framework(source: str) -> str:
    """Normalize one HITRUST authoritative-source name to a framework key.

    The same answer as `normalize_framework`, which is the point: these were
    two functions with two tables and they disagreed about what `NIST 800-171`
    keyed to. Kept as a name because the HITRUST importer reads better for it.
    """
    return normalize_framework(source) if source.strip() else ""


def _requirement_sources(control: Control) -> list[tuple[str, dict[str, list[str]]]]:
    """Every (requirement_id, {framework: [ids]}) a Control's levels carry.

    HITRUST publishes its crosswalk per *requirement statement* rather than
    per control, because two levels of the same control reference map to
    different places — Level 1 to a handful of 800-53 controls, Level
    FedRAMP to the FedRAMP baseline. Folding them into the control would
    claim mappings for levels that never had them.
    """
    out = []
    for requirement in control.requirements:
        grouped: dict[str, list[str]] = {}
        for source, identifiers in requirement.mappings.items():
            framework = hitrust_framework(source)
            if not framework:
                continue
            merged = grouped.setdefault(framework, [])
            for identifier in identifiers:
                if identifier not in merged:
                    merged.append(identifier)
        if grouped:
            out.append((requirement.requirement_id, grouped))
    return out


def requirement_crosswalk(controls: list[Control]) -> dict[str, dict[str, list[str]]]:
    """`{requirement_id: {framework: [ids]}}` for every level-scoped mapping.

    Kept separate from `build_crosswalk`'s NIST-anchored table on purpose.
    A full CSF library maps to some eighty-odd authoritative sources, and
    pouring all of them into the 800-53 table would bury the frameworks
    this project actually synthesizes against under sources nobody here has
    a catalog for. This function is where the rest stays reachable.
    """
    return {
        requirement_id: mappings
        for control in controls
        for requirement_id, mappings in _requirement_sources(control)
    }


def build_crosswalk(controls: list[Control]) -> dict[str, dict[str, list[str]]]:
    crosswalk: dict[str, dict[str, list[str]]] = {}

    for control in controls:
        if not _is_nist(control.framework):
            continue
        for nist_id, source_crosswalk in _crosswalk_sources(control):
            entry = crosswalk.setdefault(nist_id, {})
            for framework, raw in source_crosswalk.items():
                # Keyed as the other two loops key it. The raw name ("HIPAA
                # Security Rule") never matched coverage's normalised key
                # ("hipaa"), so a mapping carried on the 800-53 side was in
                # the crosswalk and invisible to every reader of it (#278).
                framework = normalize_framework(framework)
                for id_ in _extract_ids(raw):
                    ids = entry.setdefault(framework, [])
                    if id_ not in ids:
                        ids.append(id_)

    for control in controls:
        if _is_nist(control.framework):
            continue
        framework = normalize_framework(control.framework)
        for requirement_id, source_crosswalk in _crosswalk_sources(control):
            # De-duplicated in the order the mapping names them, not in a set.
            # A set iterates in hash order, and string hashing is randomised
            # per process, so `map` wrote crosswalk.json with its keys in a
            # different order on every run — the same crosswalk, a new diff.
            nist_ids: dict[str, None] = {}
            for key, raw in source_crosswalk.items():
                if _is_nist(key):
                    nist_ids.update(dict.fromkeys(_extract_ids(raw)))
            for nist_id in nist_ids:
                ids = crosswalk.setdefault(nist_id, {}).setdefault(framework, [])
                if requirement_id not in ids:
                    ids.append(requirement_id)

    # Level-scoped mappings, which only HITRUST publishes. Anchored on the
    # 800-53 identifiers a requirement names, and recorded under the
    # requirement's own id so a reader can see which *level* of a control
    # reference earned the mapping — "01.a Level 1" and "01.a Level FedRAMP"
    # are different answers to the same question.
    for control in controls:
        if _is_nist(control.framework):
            continue
        framework = normalize_framework(control.framework)
        for requirement_id, mappings in _requirement_sources(control):
            for nist_id in mappings.get(NIST_ANCHOR, []):
                for anchor in _extract_ids(nist_id):
                    ids = crosswalk.setdefault(anchor, {}).setdefault(framework, [])
                    if requirement_id not in ids:
                        ids.append(requirement_id)

    return crosswalk
