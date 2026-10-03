"""Parse NIST's official OSCAL edition of SP 800-53 into this project's
common Control/ControlEnhancement schema.

`ingest/nist_vault_loader.py` parses one specific Obsidian vault's markdown
notes, which makes it useful only to someone who already has that vault.
This loader reads NIST's own machine-readable catalog instead, so the
800-53 data can be populated from scratch by anyone:

    policyforge etl-oscal

Source is the `usnistgov/oscal-content` repository — the authoritative
OSCAL release of SP 800-53 Rev 5 plus the Low/Moderate/High baseline
profiles. A US government work, so public domain, same basis as the eCFR
and CPRT sources used elsewhere in `ingest/`.

OSCAL shapes that need translating into this project's flatter schema:

* **Identifiers.** OSCAL ids are lowercase and dotted (`ac-2`, `ac-2.1`).
  The human-facing label is carried in a `props` entry, and each control
  has *three* of them: a zero-padded form ("AC-02"), an SP 800-53A form,
  and the plain form NIST prints in the publication ("AC-2"). Only the
  last has no `class` key, and that's the one the rest of this codebase
  uses — see `mapping/crosswalk.py`'s ID regex.
* **Statements.** Control text is a `parts` tree, not a string: a
  `statement` part containing labelled `item` parts ("a.", "b.", then
  nested "1.", "2."). These are flattened back into readable prose with
  their labels preserved, since that lettering is what an SSP's
  implementation narrative refers to.
* **Parameters.** Prose contains `{{ insert: param, ac-02_odp.01 }}`
  placeholders. Each resolves either to an assignment (a `label`) or to a
  selection (a `select` with `choice` values), and is rendered the way NIST
  prints them: "[Assignment: organization-defined ...]" /
  "[Selection (one or more): ...]". Leaving the raw placeholders in would
  put OSCAL internals into a compliance deliverable.
* **Withdrawn controls.** 182 enhancements are marked `status: withdrawn`
  and carry no statement text. They are excluded: they aren't part of Rev 5
  any more, they appear in no baseline, and listing them in an SSP would
  invite implementation narratives for controls that no longer exist. The
  count is reported by `parse_oscal_catalog` rather than silently dropped.

**This module reads more than one NIST catalog, and they do not agree with
each other.** See `OscalDialect`: the bullet above about unclassed `label`
props is true of 800-53 and false of 800-171, where the unclassed label is
the control's *title* with the citation in brackets. Everything catalog-
specific lives in a dialect, and `parse_oscal_catalog` defaults to 800-53
so callers that predate this keep their behaviour exactly.

One visible consequence, stated so nobody "fixes" it: **800-53 statements
render `a.` and 800-171's render `a`.** That is NIST's difference, not
ours — 800-53's label prop literally contains the dot and 800-171's
segment does not. Adding one would put a character on the page that NIST
did not, in a catalog whose whole pitch is that a citation can be traced
back. The awkward line is the honest one.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from .schema import Control, ControlEnhancement

#: The upstream revision this catalog is read from.
#:
#: Was `main`, which meant the bundled `controls.json` could not be traced to
#: anything: a reviewer looking at it could not tell an upstream revision
#: from an upstream compromise, and re-running the ETL a month apart could
#: produce two different catalogs with no record of why. The monthly drift
#: job turns any change into a red build, which is a good compensating
#: control and not the same as provenance.
#:
#: Pinned to a release tag rather than a bare commit because the tag is what
#: NIST publishes and what a reviewer can look up. Verified before pinning:
#: at the time of the change the catalog bytes at `v1.5.0` and at `main` were
#: identical (sha256 01f37cf90ea9…, 10,442,037 bytes), so this changed
#: nothing about the content — only about whether it can be named.
#:
#: Moving this is a deliberate act: bump the ref, re-run `etl-oscal`, and
#: let the diff on `controls.json` be reviewed.
OSCAL_REF = "v1.5.0"

_OSCAL_ROOT = f"https://raw.githubusercontent.com/usnistgov/oscal-content/{OSCAL_REF}"
_REV5_JSON = "/nist.gov/SP800-53/rev5/json"

CATALOG_URL = f"{_OSCAL_ROOT}{_REV5_JSON}/NIST_SP-800-53_rev5_catalog.json"
BASELINE_URLS = {
    "Low": f"{_OSCAL_ROOT}{_REV5_JSON}/NIST_SP-800-53_rev5_LOW-baseline_profile.json",
    "Moderate": f"{_OSCAL_ROOT}{_REV5_JSON}/NIST_SP-800-53_rev5_MODERATE-baseline_profile.json",
    "High": f"{_OSCAL_ROOT}{_REV5_JSON}/NIST_SP-800-53_rev5_HIGH-baseline_profile.json",
}
# Baselines are cumulative, so ordering them least-to-most protective keeps
# the rendered value readable ("Low, Moderate, High" rather than set order).
BASELINE_ORDER = ("Low", "Moderate", "High")

_PARAM_INSERT_RE = re.compile(r"\{\{\s*insert:\s*param,\s*([^\s}]+)\s*\}\}")

#: 800-171 rev 3's OSCAL, for contrast with everything above.
_SP800_171_REV3_JSON = "/nist.gov/SP800-171/rev3/json"
CATALOG_URL_800_171 = f"{_OSCAL_ROOT}{_SP800_171_REV3_JSON}/NIST_SP800-171_rev3_catalog.json"
#: Written out rather than templated from the framework name. NIST spells
#: the two files differently — `NIST_SP-800-53_rev5_catalog.json` against
#: `NIST_SP800-171_rev3_catalog.json`, note where the hyphen goes — so a
#: URL built by substitution 404s. `tests/test_oscal_loader.py` pins both
#: as literals for the same reason `test_ecfr_fetch.py` does: a change of
#: behaviour should fail, not a change of shape.

#: NIST CSF 2.0's OSCAL (#408), from the same repository and tag as 800-53.
#: Written out, as the two above are: NIST names the directory `CSF/v2.0`.
CATALOG_URL_CSF_2_0 = f"{_OSCAL_ROOT}/nist.gov/CSF/v2.0/json/NIST_CSF_v2.0_catalog.json"

#: 800-171 rev 3 publishes **no baseline profiles**. 800-53 ships Low,
#: Moderate and High; `nist.gov/SP800-171/rev3/json/` contains the catalog
#: and a `-min` variant and nothing else. So `baseline` is empty on every
#: 800-171 control, and that empty is a property of the source rather than
#: a parse that came up short.


@dataclass(frozen=True)
class OscalDialect:
    """How one NIST catalog spells the things every NIST catalog has.

    **NIST's own catalogs disagree about what an unclassed `label` prop
    holds**, and that is the whole reason this type exists. The next person
    will assume it is uniform, because the brief for 800-171 did:

        800-53 rev 5   labels: AC-01 (zero-padded), AC-1 (unclassed),
                               AC-01 (sp800-53a);  group label: "AC"
        800-171 rev 3  labels: "Account Management (03.01.01)" — the only
                               one;  group label: "Access Control (03.01)"

    So on 800-53 the unclassed label *is* the citation, and on 800-171 it
    is the title with the citation in brackets. Reading it the 800-53 way
    does not fail: it yields 97 well-formed controls whose `control_id` is
    a sentence, with zero empty statements and zero unresolved parameters.
    Every count check passes. That is the failure this type prevents.
    """

    #: `Control.framework`, and the key `mapping/crosswalk.py` resolves.
    framework: str
    #: Formats `Control.framework_version` from the catalog's own metadata
    #: version, so the published revision travels with the parse.
    version_label: Callable[[str], str]
    #: Where the catalog was read from, recorded as `source_path`.
    source_url: str
    #: The citation for one control or enhancement, from its OSCAL node.
    identifier: Callable[[dict], str]
    #: The short family name, from the OSCAL group node.
    family_abbr: Callable[[dict], str]
    #: A part's rendered label. Returning "" drops it.
    part_label: Callable[[str, str], str] = lambda label, _identifier: label
    #: Whether this catalog names its 800-53 source controls as
    #: `rel="reference"` links (#259). Only 800-171 rev 3 does: 800-53's own
    #: reference links are literature, and must never become a mapping.
    references_800_53: bool = False


def _prop(item: dict, name: str) -> str | None:
    """The first value of `name` in `item`'s props, or None."""
    for prop in item.get("props", []):
        if prop.get("name") == name:
            return prop.get("value")
    return None


def _sort_id(item: dict) -> str:
    """The `sort-id` prop, which is 800-171's actual citation.

    `03.01.01` on a control, `03.01` on a group. Verified present on every
    one of the 130 controls in rev 3, so it is a strategy rather than a
    lucky sample.
    """
    return _prop(item, "sort-id") or item.get("id", "").upper()


def _strip_control_id_from_label(label: str, identifier: str) -> str:
    """`SR-03.01.01.a` -> `a`, keeping anything that does not fit.

    800-171 labels its sub-items with the control's own id followed by the
    position: the reader already has the id, so what the label adds is the
    `a`. 800-53 renders `a.` and the catalogs should read alike.

    **Measured before it was relied on**, across every live control in rev
    3: 252 part labels, 252 matching `<prefix><identifier>.<segment>`, one
    distinct prefix (`SR-`), zero exceptions. A label that does not fit is
    returned untouched rather than mangled, so if NIST ever puts something
    else there it survives and is visible.
    """
    match = re.fullmatch(rf".*?{re.escape(identifier)}\.(.+)", label)
    return match.group(1) if match else label


#: 800-53 rev 5, spelled out so it is a dialect like any other rather than
#: the one the module is secretly about. **Every value here reproduces what
#: this module did before dialects existed**, which is the property
#: `test_the_800_53_dialect_reproduces_the_previous_behaviour` holds.
NIST_800_53_REV5 = OscalDialect(
    framework="NIST 800-53",
    version_label=lambda version: f"Rev 5 ({version})",
    source_url=CATALOG_URL,
    identifier=lambda item: _plain_label(item) or item["id"].upper(),
    family_abbr=lambda group: group.get("id", "").upper(),
)

NIST_800_171_REV3 = OscalDialect(
    framework="NIST 800-171",
    version_label=lambda version: f"Rev 3 ({version})",
    source_url=CATALOG_URL_800_171,
    identifier=_sort_id,
    family_abbr=_sort_id,
    part_label=_strip_control_id_from_label,
    references_800_53=True,
)

#: NIST CSF 2.0 (#408). **The node's own `id` is the citation**: `GV.OC` on a
#: category, `GV.OC-01` on a subcategory, `GV` on a function. The unclassed
#: `label` is the 800-171 trap again (`Organizational Context (GV.OC)`), and
#: read the 800-53 way it would be every category's `control_id` (measured
#: on #408). Categories are this schema's controls, subcategories their
#: enhancements, functions the families. Implementation examples are
#: `example` parts, not the `statement`, and are not carried.
NIST_CSF_2_0 = OscalDialect(
    framework="NIST CSF 2.0",
    version_label=lambda version: f"2.0 ({version})",
    source_url=CATALOG_URL_CSF_2_0,
    identifier=lambda item: item["id"],
    family_abbr=lambda group: group.get("id", "").upper(),
)

#: The key a 800-171 requirement's `source_crosswalk` names 800-53 under, as
#: the HIPAA catalog's does; `mapping/crosswalk.py` normalises it to its anchor.
CROSSWALK_KEY_800_53 = "nist-800-53"

#: A back-matter title that IS a 800-53 control or enhancement id. NIST writes
#: them zero-padded (`AC-02`, `AC-02(03)`); the shipped 800-53 catalog does
#: not (`AC-2`, `AC-2(3)`), so the digits are captured without leading zeros.
#: Read as written, only 22 of rev 3's 157 links resolve -- a partial crosswalk
#: that looks healthier than an empty one (80 and 9b on #259).
_REFERENCE_800_53 = re.compile(r"^([A-Z]{2})-0*(\d+)(?:\(0*(\d+)\))?$")

#: The other titles rev 3's reference links carry: publications (`SP 800-63-3`,
#: `IR 7874`, `FIPS 199`), 199 of them. **In the OSCAL, the title's shape is
#: the only thing that tells a control from a publication** -- they share one
#: `rel="reference"` list -- so a title that is neither is refused, not guessed.
_REFERENCE_LITERATURE = re.compile(r"^(?:SP|IR|FIPS)\s")


def _reference_crosswalk(item: dict, titles: dict[str, str], identifier: str) -> dict[str, str]:
    """The 800-53 ids a 800-171 requirement cites, as a `source_crosswalk`.

    NIST asserts these itself, in the file this catalog is pinned to, so
    carrying them is not asserting what the source withheld (80 on #259).
    """
    ids: list[str] = []
    for link in item.get("links", []):
        if link.get("rel") != "reference":
            continue
        title = titles.get(link.get("href", "").lstrip("#"), "")
        match = _REFERENCE_800_53.match(title)
        if match:
            family, number, enhancement = match.groups()
            label = f"{family}-{number}" + (f"({enhancement})" if enhancement else "")
            if label not in ids:
                ids.append(label)
        elif not _REFERENCE_LITERATURE.match(title):
            raise ValueError(
                f"{identifier} has a reference whose back-matter title is neither a 800-53 "
                f"id nor a publication: {title!r}. The title's shape is the only thing that "
                "separates the two in this catalog, so it is refused rather than guessed."
            )
    return {CROSSWALK_KEY_800_53: ", ".join(ids)} if ids else {}


def _plain_label(item: dict) -> str | None:
    """The unpadded, publication-style label ("AC-2", "AC-2(1)").

    OSCAL carries several labels per control, distinguished by `class`; the
    unclassed one is the form NIST prints and this codebase uses.
    """
    for prop in item.get("props", []):
        if prop.get("name") == "label" and "class" not in prop:
            return prop["value"]
    # Fall back to any label rather than losing the control entirely.
    for prop in item.get("props", []):
        if prop.get("name") == "label":
            return prop["value"]
    return None


def _is_withdrawn(item: dict) -> bool:
    return any(
        prop.get("name") == "status" and prop.get("value") == "withdrawn"
        for prop in item.get("props", [])
    )


def _param_text(param: dict, params: dict[str, dict], seen: frozenset[str] = frozenset()) -> str:
    """Render one parameter the way SP 800-53 prints it.

    Selections nest: several controls (AC-7 among them) have `choice` values
    that themselves contain `{{ insert: param, ... }}` references, so choices
    are resolved recursively. `seen` guards against a parameter cycle, which
    would otherwise recurse forever on malformed input.
    """
    param_id = param.get("id", "")
    if param_id in seen:
        return "[Assignment: organization-defined parameter]"
    seen = seen | {param_id}

    if "select" in param:
        select = param["select"]
        choices = "; ".join(
            _resolve_params(choice, params, seen).strip() for choice in select.get("choice", [])
        )
        qualifier = " (one or more)" if select.get("how-many") == "one-or-more" else ""
        return f"[Selection{qualifier}: {choices}]"
    label = param.get("label")
    if label:
        # Most labels are the bare noun phrase ("frequency", "personnel or
        # roles") and need the "organization-defined" lead-in that SP 800-53
        # prints. A minority (141 of 1467 in Rev 5.2.0) already include it,
        # and prepending unconditionally would render "[Assignment:
        # organization-defined organization-defined personnel or roles]".
        if label.lower().startswith("organization-defined"):
            return f"[Assignment: {label}]"
        return f"[Assignment: organization-defined {label}]"
    return "[Assignment: organization-defined parameter]"


def _resolve_params(prose: str, params: dict[str, dict], seen: frozenset[str] = frozenset()) -> str:
    def replace(match: re.Match) -> str:
        param = params.get(match.group(1))
        return _param_text(param, params, seen) if param else match.group(0)

    return _PARAM_INSERT_RE.sub(replace, prose)


def _render_parts(
    parts: list[dict],
    params: dict[str, dict],
    depth: int = 0,
    *,
    dialect: OscalDialect | None = None,
    identifier: str = "",
) -> list[str]:
    """Flatten a `statement`/`guidance` parts tree into labelled lines."""
    lines: list[str] = []
    for part in parts:
        label = _plain_label(part)
        if label and dialect is not None:
            label = dialect.part_label(label, identifier)
        prose = _resolve_params(part.get("prose", ""), params).strip()
        if prose:
            indent = "  " * depth
            lines.append(f"{indent}{label} {prose}" if label else f"{indent}{prose}")
        if part.get("parts"):
            lines.extend(
                _render_parts(
                    part["parts"], params, depth + 1, dialect=dialect, identifier=identifier
                )
            )
    return lines


def _part_text(
    item: dict,
    name: str,
    params: dict[str, dict],
    *,
    dialect: OscalDialect | None = None,
    identifier: str = "",
) -> str:
    """Rendered text of the named top-level part (`statement`, `guidance`)."""
    for part in item.get("parts", []):
        if part.get("name") != name:
            continue
        if part.get("parts"):
            return "\n".join(
                _render_parts(part["parts"], params, dialect=dialect, identifier=identifier)
            )
        return _resolve_params(part.get("prose", ""), params).strip()
    return ""


def _related_controls(item: dict) -> list[str]:
    """OSCAL links `rel="related"` point at fragment ids (`#ac-3`); convert
    them to the label form the rest of the pipeline uses."""
    related = []
    for link in item.get("links", []):
        if link.get("rel") == "related":
            oscal_id = link["href"].lstrip("#")
            label = oscal_id_to_label(oscal_id)
            if label not in related:
                related.append(label)
    return related


def oscal_id_to_label(oscal_id: str) -> str:
    """Convert an OSCAL id to a label: "ac-2" -> "AC-2", "ac-2.1" -> "AC-2(1)".

    Used for link targets, whose labels aren't available inline the way a
    control's own `props` are.
    """
    match = re.fullmatch(r"([a-z]{2})-(\d+)(?:\.(\d+))?", oscal_id)
    if match is None:
        return oscal_id.upper()
    family, number, enhancement = match.groups()
    label = f"{family.upper()}-{int(number)}"
    return f"{label}({int(enhancement)})" if enhancement else label


def parse_baseline_profile(profile_json: dict) -> set[str]:
    """The set of OSCAL control ids a baseline profile includes."""
    ids: set[str] = set()
    for import_ in profile_json["profile"].get("imports", []):
        for include in import_.get("include-controls", []):
            ids.update(include.get("with-ids", []))
    return ids


def _baseline_label(oscal_id: str, baselines: dict[str, set[str]]) -> str:
    names = [name for name in BASELINE_ORDER if oscal_id in baselines.get(name, ())]
    # Any baseline not covered by the standard three is appended as-is so a
    # custom profile passed in by a caller still shows up.
    names += [n for n in baselines if n not in BASELINE_ORDER and oscal_id in baselines[n]]
    return ", ".join(names)


def parse_oscal_catalog(
    catalog_json: dict,
    baselines: dict[str, set[str]] | None = None,
    *,
    dialect: OscalDialect = NIST_800_53_REV5,
) -> tuple[list[Control], int]:
    """Parse an OSCAL catalog into Controls.

    Returns `(controls, withdrawn_count)` — the withdrawn tally is returned
    rather than logged so the caller can report it (see `etl-oscal` in
    cli/etl.py) instead of the exclusion being invisible.

    `dialect` defaults to 800-53 rev 5, so every existing caller keeps the
    behaviour it had. See `OscalDialect` for why one is needed at all:
    reading 800-171 with 800-53's rules does not fail, it produces a
    well-formed catalog whose identifiers are sentences.
    """
    catalog = catalog_json["catalog"]
    version = catalog["metadata"]["version"]
    baselines = baselines or {}
    titles = {
        r["uuid"]: r.get("title", "")
        for r in catalog.get("back-matter", {}).get("resources", [])
        if "uuid" in r
    }

    controls: list[Control] = []
    withdrawn = 0

    for group in catalog.get("groups", []):
        family = group.get("title", "")
        family_abbr = dialect.family_abbr(group)

        for raw_control in group.get("controls", []):
            if _is_withdrawn(raw_control):
                # Count the whole subtree, so the reported tally matches the
                # catalog's own withdrawn count rather than omitting the
                # enhancements that go with a withdrawn control.
                withdrawn += 1 + len(raw_control.get("controls", []))
                continue

            # Parameter scope is the control *and all of its enhancements*
            # together, not each item in isolation: SC-42(2)'s statement
            # references `sc-42.01_odp`, a parameter defined on its sibling
            # SC-42(1). Ids are namespaced by their owner, so pooling them
            # can't collide.
            params = {p["id"]: p for p in raw_control.get("params", [])}
            for raw_enhancement in raw_control.get("controls", []):
                params.update({p["id"]: p for p in raw_enhancement.get("params", [])})
            control_id = dialect.identifier(raw_control)

            enhancements: list[ControlEnhancement] = []
            for raw_enhancement in raw_control.get("controls", []):
                if _is_withdrawn(raw_enhancement):
                    withdrawn += 1
                    continue
                enhancement_id = dialect.identifier(raw_enhancement)
                enhancements.append(
                    ControlEnhancement(
                        enhancement_id=enhancement_id,
                        title=raw_enhancement.get("title", ""),
                        baseline=_baseline_label(raw_enhancement["id"], baselines),
                        description=_part_text(
                            raw_enhancement,
                            "statement",
                            params,
                            dialect=dialect,
                            identifier=enhancement_id,
                        ),
                    )
                )

            controls.append(
                Control(
                    control_id=control_id,
                    title=raw_control.get("title", ""),
                    framework=dialect.framework,
                    framework_version=dialect.version_label(version),
                    family=family,
                    family_abbr=family_abbr,
                    baseline=_baseline_label(raw_control["id"], baselines),
                    control_statement=_part_text(
                        raw_control, "statement", params, dialect=dialect, identifier=control_id
                    ),
                    discussion=_part_text(
                        raw_control, "guidance", params, dialect=dialect, identifier=control_id
                    ),
                    enhancements=enhancements,
                    related_controls=_related_controls(raw_control),
                    source_crosswalk=(
                        _reference_crosswalk(raw_control, titles, control_id)
                        if dialect.references_800_53
                        else {}
                    ),
                    source_path=dialect.source_url,
                )
            )

    _require_every_control(catalog, controls, withdrawn)
    return controls, withdrawn


def _declared_controls(node: dict) -> int:
    """Every control node in the OSCAL tree, at any nesting depth.

    Counted **without** consulting the dialect, the identifier rules or
    the withdrawal predicate — so it is what the document says it holds,
    independent of anything this parser believes about it.
    """
    total = 0
    for control in node.get("controls", []) or []:
        total += 1 + _declared_controls(control)
    for group in node.get("groups", []) or []:
        total += _declared_controls(group)
    return total


def _require_every_control(catalog: dict, controls: list[Control], withdrawn: int) -> None:
    """Refuse a parse that dropped a control the catalog declares.

    **Emitted plus withdrawn must equal declared.** Every control in the
    source is either published or deliberately excluded, and a third
    outcome — silently absent — is the one this exists to make
    impossible.

    The hazard is specific here rather than hypothetical. A dialect
    decides how identifiers and families are read, and reading 800-171
    with 800-53's rules *does not fail*: it produces a well-formed
    catalog whose identifiers are sentences. `OscalDialect` was added
    because of that. A dialect mismatch that instead caused controls to
    be skipped would produce a **short** catalog, and nothing in this
    module would notice — every entry in it would still be well-formed.

    Both sides come from the document: `_declared_controls` walks the
    JSON tree knowing nothing about identifiers. So there is no count to
    maintain and nothing that can go stale, which is the same argument
    `part2_loader._require_sections` makes for eCFR.
    """
    declared = _declared_controls(catalog)
    emitted = len(controls) + sum(len(control.enhancements) for control in controls)
    if emitted + withdrawn != declared:
        raise ValueError(
            f"the catalog declares {declared} control(s); {emitted} were emitted "
            f"and {withdrawn} recorded withdrawn, leaving "
            f"{declared - emitted - withdrawn} unaccounted for. A control was "
            f"neither published nor deliberately excluded, which usually means "
            f"the dialect does not match the catalog being read."
        )


#: CSF 2.0's live Core, as NIST states it in CSWP 29 and as four instruments
#: agreed id for id (f8 on #408): 6 functions, 22 categories, 106
#: subcategories, after the 91 withdrawn CSF 1.1 elements are excluded.
CSF_2_0_EXTENT = (6, 22, 106)

#: A CSF 2.0 id: a function `GV`, a category `GV.OC`, a subcategory `GV.OC-01`.
_CSF_ID = re.compile(r"^[A-Z]{2}(?:\.[A-Z]{2}(?:-\d{2})?)?$")


def require_csf_2_0_extent(controls: list[Control]) -> None:
    """Refuse a CSF 2.0 parse that is not exactly 6 / 22 / 106, or whose ids
    are not CSF-shaped (#408, 80's ruling: as ONC's re-land does).

    **Exactly, in both directions.** Fewer loses outcomes; MORE usually
    means withdrawn 1.1 elements got through (91 of them sit in the same
    file with `status: withdrawn`), and a catalog carrying `ID.AM-06`
    publishes an id CSF 2.0 no longer has. The generic
    `_require_every_control` only refuses the short direction.
    """
    families = {c.family_abbr for c in controls}
    enhancements = [e for c in controls for e in c.enhancements]
    found = (len(families), len(controls), len(enhancements))
    if found != CSF_2_0_EXTENT:
        raise ValueError(
            f"CSF 2.0 parsed as {found[0]} functions, {found[1]} categories and "
            f"{found[2]} subcategories; the Core is {CSF_2_0_EXTENT[0]}/"
            f"{CSF_2_0_EXTENT[1]}/{CSF_2_0_EXTENT[2]}. Refusing to write it."
        )
    ids = [*families, *(c.control_id for c in controls), *(e.enhancement_id for e in enhancements)]
    malformed = [i for i in ids if not _CSF_ID.match(i)]
    if malformed:
        raise ValueError(f"CSF 2.0 ids not CSF-shaped (a dialect mismatch?): {malformed[:5]}")


def fetch_csf_2_0_catalog(*, url: str = CATALOG_URL_CSF_2_0) -> dict:
    """Fetch NIST's OSCAL edition of CSF 2.0 (#408)."""
    import requests

    response = requests.get(url, timeout=120)
    response.raise_for_status()
    return response.json()


def fetch_oscal_catalog(*, url: str = CATALOG_URL) -> dict:
    """Fetch NIST's OSCAL 800-53 catalog.

    Kept separate from `parse_oscal_catalog` — network access lives only in
    the `fetch_*` functions here — so the parser stays pure and testable
    offline against a fixture.
    """
    import requests

    response = requests.get(url, timeout=120)
    response.raise_for_status()
    return response.json()


def fetch_800_171_catalog(*, url: str = CATALOG_URL_800_171) -> dict:
    """Fetch NIST's OSCAL edition of SP 800-171 rev 3.

    A separate wrapper rather than a parameter on `fetch_oscal_catalog`,
    for the reason `ingest/ecfr.py` keeps its per-regulation wrappers:
    "which URL is 800-171" is a property of the publication, not a choice
    an ETL command should be making at the call site.

    **There is no baseline counterpart.** 800-171 rev 3 publishes no
    profiles, so there is nothing for `fetch_oscal_baselines` to read.
    """
    import requests

    response = requests.get(url, timeout=120)
    response.raise_for_status()
    return response.json()


def fetch_oscal_baselines(*, urls: dict[str, str] | None = None) -> dict[str, set[str]]:
    """Fetch the Low/Moderate/High baseline profiles as id sets."""
    import requests

    baselines = {}
    for name, url in (urls or BASELINE_URLS).items():
        response = requests.get(url, timeout=120)
        response.raise_for_status()
        baselines[name] = parse_baseline_profile(response.json())
    return baselines
