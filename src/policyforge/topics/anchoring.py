"""Which ids a topic may anchor to own a requirement: one rule for every caller (#377).

"Which control does this id belong to" (`AC-2(3)` -> `AC-2`, `Govern 1.1` ->
`Govern 1`) was written separately at least seven times: coverage, bundles,
satisfies, drift, discover, the programme's parameter scope, and drift's
own `_topic_keys`. Each copy was found broken on its own (#312, #318, #335,
#339, #345, #369), and the commonest break was the same one: **a rule that
reads no framework**. `Govern 1.1` is an AI RMF subcategory under the
category `Govern 1`, and it is also the id of the Playbook's suggestions for
that subcategory, which no topic owns. A rule given only the id cannot tell
them apart, so it either misses the Core (discover, #345) or claims the
Playbook (programme, #336).

So the rule takes the framework, and the grammar is chosen by it:

    anchor_keys("AC-2(3)", "NIST 800-53")              {"AC-2(3)", "AC-2"}
    anchor_keys("Govern 1.1", "NIST AI RMF")           {"Govern 1.1", "Govern 1"}
    anchor_keys("Govern 1.1", "NIST AI RMF Playbook")  set()

A topic anchoring the parent owns the child, and a topic anchoring the child
owns it directly; which of the two wins is the caller's question (coverage
and bundles prefer the direct claim).

**Two breadths, named, because they answer different questions** (80's
ruling on #377):

- `anchor_keys` is **topic reach**: which team owns this. Only the catalogs
  a topic may anchor, and only the id and the control it hangs off.
- `family_of` is **document reach**: which documents to re-read when this
  changes. Deliberately broader, because missing a document is the
  dangerous direction: the **whole family** within the change's own
  framework, for the catalogs that have a family grammar. `AC-2(1)`
  reaches a document citing its sibling `AC-2(3)`; `Govern 1.3` reaches one
  citing `Govern 1` or any `Govern 1.x`; a Playbook action reaches Playbook
  citations of the same subcategory and never the Core's.

  **The regulatory catalogs have no grammar here; they declare a unit**
  (#423, 80's ruling): HIPAA, 42 CFR Part 2, Information Blocking and
  ONC 170.315 nest their ids several levels deep, and their catalogs
  mostly omit the parents (`164.308(a)(1)` is cited, `164.308(a)(1)(i)` is
  the id). Each `framework.yaml` names the unit the regulation calls a
  requirement (`family:`), and `regulatory_families` below resolves a
  citation to it, a paragraph the catalog does not carry included.

Ingest code that builds a catalog's own hierarchy (`ingest/ai_rmf.py`)
reads the source, not a topic registry or a document, and stays its own.
"""

from __future__ import annotations

import re

from policyforge.mapping.crosswalk import NIST_ANCHOR, TOPIC_ANCHORS, normalize_framework

#: How a child id names its parent, per catalog a topic may anchor. Keyed by
#: normalized framework, and **held equal to `TOPIC_ANCHORS`** by
#: `tests/test_anchoring.py`.
#:
#: **A grammar this does not know silently claims nothing** (moved here from
#: `coverage.py`, where it was learned). A topic anchoring `Govern 1` used to
#: report seven orphans: no error, a plausible number, and a topic author
#: would have "fixed" it by anchoring all seven subcategories, arriving at a
#: correct-looking registry built around a defect. The workaround looks like
#: diligence, which is what makes the silence expensive. Hence the test that
#: a catalog made anchorable cannot be missing from this table.
_PARENT_RES: dict[str, re.Pattern[str]] = {
    # AC-2(1) -> AC-2
    "nist-800-53": re.compile(r"^([A-Za-z]{2}-\d+)\(\d+\)$"),
    # Govern 1.1 -> Govern 1
    "nist-ai-rmf": re.compile(r"^([A-Za-z]+ \d+)\.\d+$"),
}


def parent_of(requirement_id: str, framework: str) -> str | None:
    """The control `requirement_id` hangs off in `framework`, or None.

    None for a top-level control, and for any framework a topic cannot
    anchor: the Playbook's `Govern 1.1` has no parent a topic could own.
    """
    rule = _PARENT_RES.get(normalize_framework(framework))
    match = rule.match(requirement_id) if rule else None
    return match.group(1) if match else None


def anchor_keys(requirement_id: str, framework: str) -> frozenset[str]:
    """The ids a topic may anchor to own `requirement_id` from `framework`.

    The id itself and its parent, for a catalog topics anchor; nothing for
    any other catalog, whose requirements a topic reaches only through the
    crosswalk to 800-53 (`ANCHOR_DECISIONS`). Ids are returned as written;
    a caller comparing case-insensitively upper-cases both sides.
    """
    if normalize_framework(framework) not in TOPIC_ANCHORS:
        return frozenset()
    parent = parent_of(requirement_id, framework)
    return frozenset({requirement_id, parent} if parent else {requirement_id})


#: The family grammar for document reach, per catalog, each by its own shape.
#: The regulatory catalogs are not here: they declare their unit in their
#: manifest (`catalog_families`, #423). 800-53's is shared by the catalogs that write 800-53 ids
#: (FedRAMP, ARC-AMPE). The Playbook nests its actions under the subcategory
#: they serve, and stops there: its family is never the Core's category.
_FAMILY_RES: dict[str, re.Pattern[str]] = {
    **_PARENT_RES,
    "fedramp": _PARENT_RES["nist-800-53"],
    "arc-ampe": _PARENT_RES["nist-800-53"],
    # Govern 1.1 Action 3 -> Govern 1.1
    "nist-ai-rmf-playbook": re.compile(r"^([A-Za-z]+ \d+\.\d+) Action \d+$"),
}


def family_of(requirement_id: str, framework: str) -> str:
    """The family `requirement_id` belongs to in `framework`, for document
    reach: the control it hangs off, or itself. Two ids in one framework with
    the same family are re-read together."""
    rule = _FAMILY_RES.get(normalize_framework(framework))
    match = rule.match(requirement_id) if rule else None
    return match.group(1) if match else requirement_id


def topic_keys(
    requirement_id: str,
    framework: str,
    crosswalk: dict[str, dict[str, list[str]]] | None = None,
    relationships: dict[tuple[str, str, str], str] | None = None,
) -> frozenset[str]:
    """The ids a topic may anchor to be reached by `requirement_id`, from ANY
    catalog: topic reach, whole (80's ruling (B) on #377).

    For a catalog topics anchor, `anchor_keys`. For any other, **through the
    crosswalk**, as `ANCHOR_DECISIONS` says those catalogs are reached: the
    anchor keys of every 800-53 control the crosswalk maps the requirement
    to, in full by `coverage.reaches_in_full` (the one reading of "partial",
    #185). `crosswalk` is `build_crosswalk` over the catalogs actually
    loaded, keyed NIST-first, so a catalog nobody loaded reaches nothing.

    A FedRAMP or ARC-AMPE id reaches what it did when this matched by id
    shape, because their crosswalks map each id to itself. HIPAA and
    800-171 reach topics for the first time, through NIST's and HHS's own
    mappings. A catalog with no crosswalk (the Playbook, Part 2, ONC,
    Information Blocking) reaches none.
    """
    if normalize_framework(framework) in TOPIC_ANCHORS:
        return anchor_keys(requirement_id, framework)
    keys: set[str] = set()
    for nist_id in crosswalked(requirement_id, framework, crosswalk, relationships):
        keys |= anchor_keys(nist_id, NIST_ANCHOR)
    return frozenset(keys)


def crosswalked(
    requirement_id: str,
    framework: str,
    crosswalk: dict[str, dict[str, list[str]]] | None,
    relationships: dict[tuple[str, str, str], str] | None = None,
) -> set[str]:
    """The 800-53 ids the loaded crosswalk maps `requirement_id` to in full.

    Empty for 800-53 itself and for any catalog with no mapping loaded.
    """
    return set(
        reverse_index(crosswalk, relationships).get(
            (normalize_framework(framework), requirement_id), ()
        )
    )


def reverse_index(
    crosswalk: dict[str, dict[str, list[str]]] | None,
    relationships: dict[tuple[str, str, str], str] | None = None,
) -> dict[tuple[str, str], frozenset[str]]:
    """`{(catalog key, requirement id): the 800-53 ids it maps to in full}`.

    The crosswalk is keyed NIST-first; every reach from another catalog asks
    it the other way round, so this is built once per crosswalk rather than
    scanned once per id.
    """
    from policyforge.topics.coverage import reaches_in_full

    index: dict[tuple[str, str], set[str]] = {}
    for nist_id, mapped in (crosswalk or {}).items():
        for key, requirement_ids in mapped.items():
            for requirement_id in requirement_ids:
                if reaches_in_full(key, requirement_id, nist_id, relationships or {}):
                    index.setdefault((key, requirement_id), set()).add(nist_id)
    return {pair: frozenset(ids) for pair, ids in index.items()}


def hubs(
    requirement_id: str,
    framework: str,
    reverse: dict[tuple[str, str], frozenset[str]],
) -> set[str]:
    """The 800-53 families a requirement stands for (80's hub rule on #377):
    its own family if it is 800-53, otherwise the families of what the
    loaded crosswalk maps it to in full. Two citations with a hub in common
    are about the same control, whichever catalog each names."""
    key = normalize_framework(framework)
    if key == NIST_ANCHOR:
        return {family_of(requirement_id, NIST_ANCHOR)}
    return {family_of(nist_id, NIST_ANCHOR) for nist_id in reverse.get((key, requirement_id), ())}


#: An 800-53-shaped id anywhere in a text. Recognition, for drift's fallback
#: on a citation that does not resolve (80's ruling on #418); the family is
#: still `family_of`'s.
_SHAPE_RE = re.compile(r"\b([A-Z]{2}-\d+(?:\(\d+\))?)")

#: A trailing statement part: `(a)` in `AC-6(1)(a)`, `(A)` in `164.308(a)(1)(ii)(A)`.
_LAST_PART_RE = re.compile(r"\([^()]*\)$")


def shaped_ids(text: str) -> list[str]:
    """Every 800-53-shaped id in `text`, in order."""
    return _SHAPE_RE.findall(text)


def is_800_53_shaped(requirement_id: str) -> bool:
    return bool(_SHAPE_RE.fullmatch(requirement_id))


def enclosing(requirement_id: str, has) -> str:
    """`requirement_id` with statement parts taken off, last first, until
    `has(id)` is true; itself if no prefix is. `AC-6(1)(a)` -> `AC-6(1)`."""
    current = requirement_id
    while not has(current):
        match = _LAST_PART_RE.search(current)
        if not match:
            return requirement_id
        current = current[: match.start()]
    return current


# ---- the regulatory catalogs (80's ruling on #423) ----

#: A section: the id before its first paragraph, `171.202(b)` -> `171.202`.
_SECTION_RE = re.compile(r"^[^(]+")
#: A criterion: a section and two paragraph levels, `170.315(b)(1)(iii)` ->
#: `170.315(b)(1)`.
_CRITERION_RE = re.compile(r"^[^(]+\([^()]*\)\([^()]*\)")
#: A top-level entry the ETL titled as its standard's specifications.
_SPEC_TITLE_RE = re.compile(r"^Implementation specifications?\b", re.IGNORECASE)


def catalog_families(controls, rule) -> dict[str, str]:
    """{id: the id of the family it belongs to} for one regulatory catalog.

    `rule` is its manifest (`registry.Framework`), declaring `family:`:

    - `section`: the section, `2.16(a)` -> `2.16`;
    - `criterion`: the criterion, `170.315(b)(1)` -> itself;
    - `structure`: the catalog's own nesting, which for HIPAA is the
      regulation's: each top-level entry a standard, its enhancements the
      standard's implementation specifications. A top-level entry titled
      "Implementation specification(s)" belongs to the standard before it.

    Then `family_overrides` (where the catalog and the regulation disagree)
    and `structure_only` (each its own family) apply. A family id is always
    a catalog id, so it has a crosswalk and a title.
    """
    families: dict[str, str] = {}
    standard = None
    for control in controls:
        cid = control.control_id
        if rule.family in ("section", "criterion"):
            for i in (cid, *(e.enhancement_id for e in control.enhancements)):
                families[i] = _head(rule.family, i)
            continue
        if _SPEC_TITLE_RE.match(control.title or "") and standard is not None:
            families[cid] = standard
        else:
            standard = families[cid] = cid
        for enhancement in control.enhancements:
            families[enhancement.enhancement_id] = families[cid]
    families.update({i: f for i, f in rule.family_overrides.items() if i in families})
    families.update({i: i for i in rule.structure_only if i in families})
    # A section head that is not itself an id (none ship today) stays a
    # family by name; `regulatory_families` returns only what is here.
    return families


def _head(unit: str, requirement_id: str) -> str:
    """`requirement_id`'s section, or its criterion (a section and two
    paragraph levels, falling back to the section for a shorter id)."""
    if unit == "criterion":
        match = _CRITERION_RE.match(requirement_id)
        if match:
            return match.group(0)
    return _SECTION_RE.match(requirement_id).group(0)


def regulatory_families(requirement_id: str, families: dict[str, str]) -> set[str]:
    """The families a citation of `requirement_id` stands for.

    - an id the catalog has: its family;
    - a statement part below one (`164.316(b)(1)(i)`): that id's family;
    - an **ancestor** the catalog does not carry, how a standard is cited
      (`164.308(a)(1)`, whose id is `164.308(a)(1)(i)`; `164.316(b)`): the
      families of every id beneath it. A section cited whole stands for all
      of its standards, so a change to any of them reaches the document.

    Empty when nothing resolves, so the citation is still reported.
    """
    if requirement_id in families:
        return {families[requirement_id]}
    inner = enclosing(requirement_id, lambda i: i in families)
    if inner in families:
        return {families[inner]}
    return {f for i, f in families.items() if i.startswith(requirement_id + "(")}


def families_for(controls, rules=None) -> dict[str, dict[str, str]]:
    """{catalog key: `catalog_families`} for each loaded catalog whose
    manifest declares a family rule. `rules` defaults to the manifests on
    disk (`registry.declared_family_rules`), read once."""
    if rules is None:
        rules = _declared_rules()
    by_key: dict[str, list] = {}
    for control in controls:
        by_key.setdefault(normalize_framework(control.framework), []).append(control)
    return {key: catalog_families(ctl, rules[key]) for key, ctl in by_key.items() if key in rules}


_RULES: dict | None = None


def _declared_rules() -> dict:
    global _RULES
    if _RULES is None:
        from policyforge.frameworks.registry import config_or_defaults, declared_family_rules

        _RULES = declared_family_rules(config_or_defaults("family rules were read"))
    return _RULES
