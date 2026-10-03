"""ingest/oscal_loader.py tests.

Two fixtures, both real NIST content rather than synthetic data:

* tests/fixtures/oscal_800-53r5_excerpt.json — a verbatim excerpt of NIST's
  OSCAL catalog (AC-1, AC-2, AC-3, AC-13, AT-1, AT-2, AT-5, SA-12), chosen
  to cover every shape the parser handles: selection and assignment
  parameters, two-level statement nesting, live and withdrawn enhancements,
  a withdrawn control with no enhancements, and a withdrawn control that
  carries 15 of them.
* tests/fixtures/oscal_800-53r5_baselines_excerpt.json — the Low/Moderate/
  High profiles filtered to those same controls, keeping the real profile
  shape.

The final section additionally asserts invariants over the full generated
data/frameworks/nist-800-53-r5/controls.json, since some properties (every
ID being well-formed, baselines matching NIST's published counts) are only
meaningful across the whole catalog.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"
CATALOG = FIXTURES / "oscal_800-53r5_excerpt.json"
BASELINES = FIXTURES / "oscal_800-53r5_baselines_excerpt.json"
NIST_DATA = (
    Path(__file__).parent.parent / "data" / "frameworks" / "nist-800-53-r5" / "controls.json"
)


def _catalog() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def _baselines() -> dict[str, set[str]]:
    from policyforge.ingest.oscal_loader import parse_baseline_profile

    raw = json.loads(BASELINES.read_text(encoding="utf-8"))
    return {name: parse_baseline_profile(profile) for name, profile in raw.items()}


def _parse():
    from policyforge.ingest.oscal_loader import parse_oscal_catalog

    controls, withdrawn = parse_oscal_catalog(_catalog(), _baselines())
    return {c.control_id: c for c in controls}, withdrawn


# --------------------------------------------------------------------------
# Identifiers
# --------------------------------------------------------------------------


def test_uses_the_unpadded_publication_label_not_the_oscal_id():
    """OSCAL ids are "ac-2"/"ac-2.1" and carry three different label props.
    The one this project uses is the unpadded form NIST prints ("AC-2"),
    which is also what mapping/crosswalk.py's ID regex matches."""
    by_id, _ = _parse()
    assert "AC-2" in by_id
    assert "ac-2" not in by_id
    assert "AC-02" not in by_id  # the zero-padded label must not win
    assert by_id["AC-2"].enhancements[0].enhancement_id == "AC-2(1)"


def test_oscal_id_to_label_converts_link_targets():
    from policyforge.ingest.oscal_loader import oscal_id_to_label

    assert oscal_id_to_label("ac-3") == "AC-3"
    assert oscal_id_to_label("ac-2.1") == "AC-2(1)"
    assert oscal_id_to_label("ac-2.13") == "AC-2(13)"


def test_related_controls_come_through_as_labels():
    by_id, _ = _parse()
    related = by_id["AC-2"].related_controls
    assert "AC-3" in related and "AC-5" in related
    assert not any(r.islower() for r in related)


# --------------------------------------------------------------------------
# Statement rendering
# --------------------------------------------------------------------------


def test_statement_parts_are_flattened_with_their_labels():
    """Control text is a parts tree in OSCAL. The a./b./c. lettering has to
    survive, because an SSP's implementation narrative is written and
    assessed part-by-part against it."""
    by_id, _ = _parse()
    statement = by_id["AC-2"].control_statement
    assert statement.startswith("a. Define and document the types of accounts")
    assert "\nb. Assign account managers;" in statement
    # Second-level nesting keeps its own numbering, indented.
    assert "  1. Authorized users of the system;" in statement


def test_assignment_parameters_are_rendered_the_way_nist_prints_them():
    by_id, _ = _parse()
    assert (
        "[Assignment: organization-defined prerequisites and criteria]"
        in by_id["AC-2"].control_statement
    )
    # No raw OSCAL placeholders may survive into a compliance deliverable.
    assert "{{ insert:" not in by_id["AC-2"].control_statement


def test_selection_parameters_render_as_selections():
    by_id, _ = _parse()
    statement = by_id["AC-1"].control_statement
    assert (
        "[Selection (one or more): organization-level; mission/business process-level" in statement
    )


def test_organization_defined_prefix_is_not_doubled():
    """141 of the catalog's 1,467 labelled parameters already begin with
    "organization-defined"; prepending unconditionally produced
    "[Assignment: organization-defined organization-defined personnel or
    roles]"."""
    by_id, _ = _parse()
    assert (
        "[Assignment: organization-defined personnel or roles]" in by_id["AC-1"].control_statement
    )
    assert "organization-defined organization-defined" not in by_id["AC-1"].control_statement


def test_parameters_nested_inside_a_selection_choice_are_resolved():
    """AC-7's selection has `choice` strings that themselves contain
    `{{ insert: param }}` references. Resolving only the outer level left raw
    OSCAL placeholders in the rendered control text."""
    by_id, _ = _parse()
    statement = by_id["AC-7"].control_statement
    assert "{{ insert:" not in statement
    assert (
        "lock the account or node for [Assignment: organization-defined time period]" in statement
    )
    assert (
        "delay next logon prompt per [Assignment: organization-defined delay algorithm]"
        in statement
    )


def test_parameter_defined_on_a_sibling_enhancement_resolves():
    """SC-42(2) references `sc-42.01_odp`, which is defined on SC-42(1) — a
    sibling, not its own or its parent's parameter list. Parameter scope is
    therefore the whole control-plus-enhancements family."""
    by_id, _ = _parse()
    sc42 = {e.enhancement_id: e for e in by_id["SC-42"].enhancements}
    assert "{{ insert:" not in sc42["SC-42(2)"].description
    assert "[Assignment: organization-defined sensors]" in sc42["SC-42(2)"].description


def test_discussion_is_captured_from_the_guidance_part():
    by_id, _ = _parse()
    assert "Examples of system account types" in by_id["AC-2"].discussion


# --------------------------------------------------------------------------
# Withdrawn controls
# --------------------------------------------------------------------------


def test_withdrawn_controls_and_enhancements_are_excluded():
    """Withdrawn items are in no baseline and carry no statement text;
    listing them would invite implementation narratives for controls that no
    longer exist in Rev 5."""
    by_id, _ = _parse()
    assert "AC-13" not in by_id  # withdrawn control
    assert "AT-5" not in by_id
    assert "SA-12" not in by_id
    assert "AC-2(10)" not in {e.enhancement_id for e in by_id["AC-2"].enhancements}


def test_withdrawn_count_includes_enhancements_of_a_withdrawn_control():
    """SA-12 is withdrawn and carries 15 enhancements. Skipping the parent
    without counting its subtree under-reported the exclusions."""
    _, withdrawn = _parse()
    # Withdrawn controls: AC-13 (1) + AT-5 (1) + SA-12 (1 itself, plus the 15
    # enhancements it carries = 16). Withdrawn enhancements under controls
    # that are themselves live: AC-2 (1) + AC-3 (2) + AC-7 (1) + SC-42 (1).
    assert withdrawn == 1 + 1 + 16 + 1 + 2 + 1 + 1
    assert withdrawn == 23


# --------------------------------------------------------------------------
# Baselines
# --------------------------------------------------------------------------


def test_baselines_are_tagged_from_the_profiles():
    by_id, _ = _parse()
    assert by_id["AC-2"].baseline == "Low, Moderate, High"
    enhancements = {e.enhancement_id: e for e in by_id["AC-2"].enhancements}
    # AC-2(1) is selected by Moderate and High but not Low.
    assert enhancements["AC-2(1)"].baseline == "Moderate, High"


def test_control_absent_from_every_baseline_has_no_baseline_label():
    by_id, _ = _parse()
    enhancements = {e.enhancement_id: e for e in by_id["AC-2"].enhancements}
    assert enhancements["AC-2(6)"].baseline == ""


def test_family_metadata_comes_from_the_group():
    by_id, _ = _parse()
    assert by_id["AC-1"].family == "Access Control"
    assert by_id["AC-1"].family_abbr == "AC"
    assert by_id["AT-1"].family == "Awareness and Training"


# --------------------------------------------------------------------------
# The generated catalog data
# --------------------------------------------------------------------------


def _shipped():
    from policyforge.ingest.schema import load_controls

    return load_controls(NIST_DATA)


def test_shipped_catalog_ids_are_all_well_formed():
    """Every ID must match the form mapping/crosswalk.py extracts, or the
    crosswalk silently fails to join."""
    pattern = re.compile(r"^[A-Z]{2}-\d+$")
    enhancement_pattern = re.compile(r"^[A-Z]{2}-\d+\(\d+\)$")
    controls = _shipped()
    assert controls, "NIST catalog data is missing — run `policyforge etl-oscal`"
    for control in controls:
        assert pattern.match(control.control_id), control.control_id
        for enhancement in control.enhancements:
            assert enhancement_pattern.match(enhancement.enhancement_id), enhancement.enhancement_id


def test_shipped_catalog_baseline_counts_match_nists_published_profiles():
    """Low/Moderate/High select 149/287/370 controls-and-enhancements. If the
    parse drops or double-counts anything, these totals move."""
    controls = _shipped()
    for baseline, expected in (("Low", 149), ("Moderate", 287), ("High", 370)):
        total = sum(1 for c in controls if baseline in (c.baseline or "")) + sum(
            1 for c in controls for e in c.enhancements if baseline in (e.baseline or "")
        )
        assert total == expected, f"{baseline}: got {total}, NIST profile has {expected}"


def test_shipped_catalog_has_no_unresolved_oscal_placeholders():
    for control in _shipped():
        assert "{{ insert:" not in control.control_statement, control.control_id
        assert "organization-defined organization-defined" not in control.control_statement


def test_hipaa_crosswalk_targets_all_exist_in_the_shipped_catalog():
    """The HIPAA crosswalk was built from CPRT's 800-53 Rev 5.1.1 data and
    this catalog is Rev 5.2.0 — an independent check that the two loaders
    agree on control identifiers."""
    from policyforge.ingest.schema import load_controls
    from policyforge.mapping.crosswalk import build_crosswalk

    hipaa = load_controls(
        Path(__file__).parent.parent
        / "data"
        / "frameworks"
        / "hipaa-security-rule"
        / "controls.json"
    )
    catalog = _shipped()
    known = {c.control_id for c in catalog} | {
        e.enhancement_id for c in catalog for e in c.enhancements
    }
    crosswalk = build_crosswalk(catalog + hipaa)
    referenced = {control_id for control_id, m in crosswalk.items() if "hipaa" in m}

    assert referenced, "expected the HIPAA crosswalk to reference NIST controls"
    assert referenced <= known, f"not in the catalog: {sorted(referenced - known)}"
